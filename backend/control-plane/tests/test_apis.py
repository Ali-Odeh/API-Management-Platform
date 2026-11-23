"""APIs (P.1): the /apis admin API, the lifecycle, routes in APIs, OpenAPI documents."""

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.apis.crud import openapi_digest
from app.apis.models import OPENAPI_MAX_BYTES, Api, ApiRoute
from app.audit.models import AuditEvent
from app.workspaces.models import Workspace
from tests.factories import (
    expect_violation,
    make_api,
    make_route,
    make_service,
    make_workspace,
)
from tests.test_gateway_config import Watch

URL = "/api/v1/workspaces/default/apis"
Headers = dict[str, str]
DOC: dict[str, Any] = {"openapi": "3.1.0", "info": {"title": "Orders", "version": "1"}, "paths": {}}


@pytest.fixture
async def routes(db: AsyncSession, workspace: Workspace) -> list[uuid.UUID]:
    """Two routes of `default`, by path: /v1/orders, /v1/refunds."""
    service = await make_service(db, workspace.id)
    return [
        (await make_route(db, workspace.id, service.id, path_prefix=path)).id
        for path in ("/v1/orders", "/v1/refunds")
    ]


async def create(client: AsyncClient, headers: Headers, **overrides: Any) -> Response:
    body = {"name": "Orders API", "slug": "orders"} | overrides
    return await client.post(URL, json=body, headers=headers)


async def events(db: AsyncSession, action: str) -> list[AuditEvent]:
    return list(await db.scalars(select(AuditEvent).where(AuditEvent.action == action)))


# ---- Creating and reading


async def test_an_api_starts_as_a_draft_with_its_routes_and_document(
    client: AsyncClient, auth_headers: Headers, routes: list[uuid.UUID], db: AsyncSession
) -> None:
    r = await create(client, auth_headers, route_ids=[str(i) for i in routes], openapi=DOC)

    assert r.status_code == 201, r.text
    api = r.json()
    assert api["status"] == "draft"
    assert api["has_openapi"] is True
    assert api["openapi"] == DOC
    assert [route["path_prefix"] for route in api["routes"]] == ["/v1/orders", "/v1/refunds"]
    [event] = await events(db, "api.create")
    assert event.changes["status"] == [None, "draft"]
    assert event.changes["openapi"] == [None, openapi_digest(DOC)]  # never the document
    assert event.changes["route_ids"] == [None, sorted(str(i) for i in routes)]


async def test_an_api_needs_no_document_or_routes(
    client: AsyncClient, auth_headers: Headers
) -> None:
    r = await create(client, auth_headers, description="Everything about orders")
    assert r.status_code == 201, r.text
    assert (r.json()["openapi"], r.json()["has_openapi"], r.json()["routes"]) == (None, False, [])


async def test_the_list_leaves_documents_out_and_goes_by_name(
    client: AsyncClient, auth_headers: Headers
) -> None:
    for name, slug in (("Zeta", "zeta"), ("Alpha", "alpha")):
        assert (await create(client, auth_headers, name=name, slug=slug, openapi=DOC)).is_success

    r = await client.get(URL, headers=auth_headers)

    assert r.status_code == 200
    assert r.json()["total"] == 2
    assert [a["name"] for a in r.json()["items"]] == ["Alpha", "Zeta"]
    assert all("openapi" not in a and a["has_openapi"] for a in r.json()["items"])


async def test_get_one_api(client: AsyncClient, auth_headers: Headers) -> None:
    created = (await create(client, auth_headers, openapi=DOC)).json()
    r = await client.get(f"{URL}/{created['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == created
    assert (await client.get(f"{URL}/{uuid.uuid4()}", headers=auth_headers)).status_code == 404


async def test_a_slug_is_unique_in_its_workspace(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession
) -> None:
    assert (await create(client, auth_headers)).status_code == 201
    r = await create(client, auth_headers, name="Other")
    assert r.status_code == 409
    assert "orders" in r.json()["detail"]
    # Another workspace may use it.
    other = await make_workspace(db, slug="other")
    await make_api(db, other.id, slug="orders")


@pytest.mark.parametrize(
    "body",
    [
        {"slug": "Orders"},  # slugs are lowercase
        {"slug": "has space"},
        {"name": ""},
        {"name": "x" * 101},
        {"description": "x" * 2001},
        {"route_ids": ["not-a-uuid"]},
    ],
)
async def test_invalid_input_is_422(
    client: AsyncClient, auth_headers: Headers, body: dict[str, Any]
) -> None:
    assert (await create(client, auth_headers, **body)).status_code == 422


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"info": {"title": "x"}}, "openapi"),
        ({"openapi": "2.0", "info": {}}, "openapi"),  # 2.0 is "swagger"
        ({"openapi": 3, "info": {}}, "openapi"),
        ({"openapi": "3.0.3"}, "info"),
        ({"openapi": "3.0.3", "info": "Orders"}, "info"),
        ([], "dict"),
    ],
)
async def test_a_document_must_say_what_it_is(
    client: AsyncClient, auth_headers: Headers, document: Any, message: str
) -> None:
    r = await create(client, auth_headers, openapi=document)
    assert r.status_code == 422
    assert message in r.text


async def test_swagger_2_documents_are_accepted(client: AsyncClient, auth_headers: Headers) -> None:
    swagger = {"swagger": "2.0", "info": {"title": "Old", "version": "1"}, "paths": {}}
    assert (await create(client, auth_headers, openapi=swagger)).status_code == 201


async def test_a_document_over_the_size_limit_is_422(
    client: AsyncClient, auth_headers: Headers
) -> None:
    # Exactly at the limit passes; one byte more doesn't.
    base = DOC | {"x-padding": ""}
    padding = OPENAPI_MAX_BYTES - len(json.dumps(base, separators=(",", ":")).encode())
    at_limit = DOC | {"x-padding": "a" * padding}
    assert (await create(client, auth_headers, openapi=at_limit)).status_code == 201
    over = DOC | {"x-padding": "a" * (padding + 1)}
    r = await create(client, auth_headers, slug="other", openapi=over)
    assert r.status_code == 422
    assert "larger than" in r.text


# ---- Routes in APIs


async def test_a_route_is_in_one_api_at_most(
    client: AsyncClient, auth_headers: Headers, routes: list[uuid.UUID]
) -> None:
    assert (await create(client, auth_headers, route_ids=[str(routes[0])])).status_code == 201
    r = await create(
        client, auth_headers, name="Refunds", slug="refunds", route_ids=[str(routes[0])]
    )
    assert r.status_code == 409
    assert r.json() == {"detail": "The route /v1/orders is already part of the API 'Orders API'"}


async def test_a_route_listed_twice_is_422(
    client: AsyncClient, auth_headers: Headers, routes: list[uuid.UUID]
) -> None:
    r = await create(client, auth_headers, route_ids=[str(routes[0])] * 2)
    assert r.status_code == 422
    assert "must not repeat" in r.text


async def test_an_unknown_route_is_422(client: AsyncClient, auth_headers: Headers) -> None:
    r = await create(client, auth_headers, route_ids=[str(uuid.uuid4())])
    assert r.status_code == 422
    assert r.json() == {"detail": "Route not found"}


async def test_patch_replaces_the_routes_as_a_whole(
    client: AsyncClient, auth_headers: Headers, routes: list[uuid.UUID], db: AsyncSession
) -> None:
    api = (await create(client, auth_headers, route_ids=[str(routes[0])])).json()

    r = await client.patch(
        f"{URL}/{api['id']}", json={"route_ids": [str(routes[1])]}, headers=auth_headers
    )

    assert r.status_code == 200, r.text
    assert [route["path_prefix"] for route in r.json()["routes"]] == ["/v1/refunds"]
    [event] = await events(db, "api.update")
    assert event.changes == {"route_ids": [[str(routes[0])], [str(routes[1])]]}
    # The freed route can join another API.
    other = await create(client, auth_headers, name="Two", slug="two", route_ids=[str(routes[0])])
    assert other.status_code == 201


async def test_the_same_routes_in_another_order_change_nothing(
    client: AsyncClient, auth_headers: Headers, routes: list[uuid.UUID], db: AsyncSession
) -> None:
    api = (await create(client, auth_headers, route_ids=[str(i) for i in routes])).json()
    r = await client.patch(
        f"{URL}/{api['id']}",
        json={"route_ids": [str(i) for i in reversed(routes)], "name": "Orders API"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert await events(db, "api.update") == []


async def test_deleting_a_route_takes_it_out_of_its_api(
    client: AsyncClient, auth_headers: Headers, routes: list[uuid.UUID]
) -> None:
    api = (await create(client, auth_headers, route_ids=[str(i) for i in routes])).json()
    r = await client.delete(f"/api/v1/workspaces/default/routes/{routes[0]}", headers=auth_headers)
    assert r.status_code == 204
    left = (await client.get(f"{URL}/{api['id']}", headers=auth_headers)).json()["routes"]
    assert [route["path_prefix"] for route in left] == ["/v1/refunds"]


async def test_deleting_an_api_keeps_its_routes(
    client: AsyncClient, auth_headers: Headers, routes: list[uuid.UUID], db: AsyncSession
) -> None:
    api = (await create(client, auth_headers, route_ids=[str(i) for i in routes])).json()

    r = await client.delete(f"{URL}/{api['id']}", headers=auth_headers)

    assert r.status_code == 204
    assert (await client.get(f"{URL}/{api['id']}", headers=auth_headers)).status_code == 404
    assert await db.scalar(select(func.count()).select_from(ApiRoute)) == 0
    routes_left = await client.get("/api/v1/workspaces/default/routes", headers=auth_headers)
    assert routes_left.json()["total"] == 2
    [event] = await events(db, "api.delete")
    assert event.changes["slug"] == ["orders", None]


# ---- The lifecycle


async def _move(client: AsyncClient, headers: Headers, api_id: str, status: str) -> Response:
    return await client.patch(f"{URL}/{api_id}", json={"status": status}, headers=headers)


async def test_the_lifecycle_goes_forward_and_a_deprecation_can_be_undone(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession
) -> None:
    api = (await create(client, auth_headers)).json()["id"]
    for status in ("published", "deprecated", "published", "deprecated"):
        r = await _move(client, auth_headers, api, status)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == status
    moves = [e.changes["status"] for e in await events(db, "api.update")]
    assert sorted(moves) == sorted(
        [
            ["draft", "published"],
            ["published", "deprecated"],
            ["deprecated", "published"],
            ["published", "deprecated"],
        ]
    )


@pytest.mark.parametrize(
    ("path", "wanted", "conflict"),
    [
        ((), "deprecated", "api_draft"),  # a draft is published first
        (("published",), "draft", "api_published"),  # developers may rely on it
        (("published", "deprecated"), "draft", "api_deprecated"),
    ],
)
async def test_other_moves_are_409_naming_where_it_is(
    client: AsyncClient,
    auth_headers: Headers,
    path: tuple[str, ...],
    wanted: str,
    conflict: str,
) -> None:
    api = (await create(client, auth_headers)).json()["id"]
    for status in path:
        assert (await _move(client, auth_headers, api, status)).status_code == 200
    r = await _move(client, auth_headers, api, wanted)
    assert r.status_code == 409
    assert r.json() == {"detail": conflict}


async def test_staying_in_the_same_status_changes_nothing(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession
) -> None:
    api = (await create(client, auth_headers)).json()["id"]
    assert (await _move(client, auth_headers, api, "draft")).status_code == 200
    assert await events(db, "api.update") == []


async def test_a_document_can_be_replaced_and_removed(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession
) -> None:
    api = (await create(client, auth_headers, openapi=DOC)).json()["id"]
    newer = DOC | {"info": {"title": "Orders", "version": "2"}}

    r = await client.patch(f"{URL}/{api}", json={"openapi": newer}, headers=auth_headers)
    assert r.json()["openapi"] == newer
    r = await client.patch(f"{URL}/{api}", json={"openapi": None}, headers=auth_headers)
    assert (r.json()["openapi"], r.json()["has_openapi"]) == (None, False)

    stored = await db.scalar(select(Api.openapi).where(Api.id == uuid.UUID(api)))
    assert stored is None  # SQL NULL, not the JSON value null
    changes = [e.changes["openapi"] for e in await events(db, "api.update")]
    assert sorted(changes, key=str) == sorted(
        [
            [openapi_digest(DOC), openapi_digest(newer)],
            [openapi_digest(newer), None],
        ],
        key=str,
    )


@pytest.mark.parametrize("field", ["name", "description", "route_ids", "status"])
async def test_patch_refuses_null(client: AsyncClient, auth_headers: Headers, field: str) -> None:
    api = (await create(client, auth_headers)).json()["id"]
    r = await client.patch(f"{URL}/{api}", json={field: None}, headers=auth_headers)
    assert r.status_code == 422


async def test_the_slug_never_changes(client: AsyncClient, auth_headers: Headers) -> None:
    api = (await create(client, auth_headers)).json()["id"]
    r = await client.patch(f"{URL}/{api}", json={"slug": "renamed"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["slug"] == "orders"


def test_the_digest_ignores_key_order() -> None:
    assert openapi_digest({"a": 1, "b": 2}) == openapi_digest({"b": 2, "a": 1})
    assert openapi_digest({"a": 1}) != openapi_digest({"a": 2})
    assert openapi_digest(None) is None


# ---- APIs are not config: the gateway serves the same either way


async def test_api_changes_never_bump_the_config(
    client: AsyncClient, auth_headers: Headers, routes: list[uuid.UUID]
) -> None:
    watch = await Watch().start()
    api = (await create(client, auth_headers, route_ids=[str(routes[0])], openapi=DOC)).json()
    await _move(client, auth_headers, api["id"], "published")
    await client.patch(
        f"{URL}/{api['id']}", json={"route_ids": [str(routes[1])]}, headers=auth_headers
    )
    await client.delete(f"{URL}/{api['id']}", headers=auth_headers)
    await watch.assert_unchanged()


# ---- What the database enforces on its own


async def test_the_database_refuses_a_link_across_workspaces(
    db: AsyncSession, workspace: Workspace, routes: list[uuid.UUID]
) -> None:
    other_id = (await make_workspace(db, slug="other")).id
    their_api = (await make_api(db, other_id)).id  # plain ids: a rollback expires objects
    mine = workspace.id
    db.add(ApiRoute(api_id=their_api, route_id=routes[0], workspace_id=other_id))
    await expect_violation(db, "fk_api_routes_route", db.commit())
    db.add(ApiRoute(api_id=their_api, route_id=routes[0], workspace_id=mine))
    await expect_violation(db, "fk_api_routes_api", db.commit())


async def test_the_database_refuses_a_document_that_is_not_an_object(
    db: AsyncSession, workspace: Workspace
) -> None:
    db.add(Api(workspace_id=workspace.id, name="X", slug="x", openapi=["not", "an", "object"]))
    await expect_violation(db, "ck_apis_openapi_object_within_size", db.commit())
