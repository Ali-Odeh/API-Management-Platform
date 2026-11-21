"""API tests for /api/v1/services."""

import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.context import AuditContext
from app.services import crud
from app.services.models import Service
from app.services.schemas import ServiceCreate
from app.workspaces.models import Workspace
from tests.factories import make_route, make_service

URL = "/api/v1/workspaces/default/services"
Headers = dict[str, str]


async def create(client: AsyncClient, headers: Headers, **overrides: Any) -> Response:
    body = {"name": "orders", "upstream_url": "http://mock-backend:9000"} | overrides
    return await client.post(URL, json=body, headers=headers)


# ---- Create


async def test_create_returns_201_with_defaults(client: AsyncClient, auth_headers: Headers) -> None:
    r = await create(client, auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "orders"
    assert body["upstream_url"] == "http://mock-backend:9000"
    assert body["timeout_ms"] == 5000  # plan default
    assert body["is_active"] is True
    uuid.UUID(body["id"])  # a real uuid
    assert body["created_at"] and body["updated_at"]


async def test_create_strips_trailing_slash(client: AsyncClient, auth_headers: Headers) -> None:
    r = await create(client, auth_headers, upstream_url="https://api.example.com/v2/")
    assert r.json()["upstream_url"] == "https://api.example.com/v2"


async def test_create_duplicate_name_is_409(client: AsyncClient, auth_headers: Headers) -> None:
    await create(client, auth_headers)
    r = await create(client, auth_headers, upstream_url="http://other:1")
    assert r.status_code == 409
    assert r.json() == {"detail": "A service named 'orders' already exists"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "Orders"},  # capital letter
        {"name": "my orders"},  # space
        {"name": ""},
        {"name": "x" * 101},
        {"upstream_url": "ftp://files:21"},
        {"upstream_url": "not a url"},
        {"upstream_url": "http://x:9000/?debug=1"},  # query string
        {"timeout_ms": 99},
        {"timeout_ms": 60_001},
    ],
)
async def test_create_invalid_input_is_422(
    client: AsyncClient, auth_headers: Headers, overrides: dict[str, Any]
) -> None:
    r = await create(client, auth_headers, **overrides)
    assert r.status_code == 422


# ---- List


async def test_list_is_paginated_newest_first(client: AsyncClient, auth_headers: Headers) -> None:
    for name in ["a", "b", "c"]:
        await create(client, auth_headers, name=name)

    page1 = (await client.get(URL, params={"page_size": 2}, headers=auth_headers)).json()
    assert page1["total"] == 3
    assert [s["name"] for s in page1["items"]] == ["c", "b"]

    page2 = (await client.get(URL, params={"page": 2, "page_size": 2}, headers=auth_headers)).json()
    assert page2["total"] == 3
    assert [s["name"] for s in page2["items"]] == ["a"]


async def test_list_empty(client: AsyncClient, auth_headers: Headers) -> None:
    r = await client.get(URL, headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


# ---- Get


async def test_get_one(client: AsyncClient, auth_headers: Headers) -> None:
    created = (await create(client, auth_headers)).json()
    r = await client.get(f"{URL}/{created['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == created


async def test_get_unknown_is_404(client: AsyncClient, auth_headers: Headers) -> None:
    r = await client.get(f"{URL}/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404
    assert r.json() == {"detail": "Service not found"}


async def test_get_bad_uuid_is_422(client: AsyncClient, auth_headers: Headers) -> None:
    r = await client.get(f"{URL}/not-a-uuid", headers=auth_headers)
    assert r.status_code == 422


# ---- Update


async def test_patch_changes_only_sent_fields(client: AsyncClient, auth_headers: Headers) -> None:
    created = (await create(client, auth_headers)).json()
    r = await client.patch(
        f"{URL}/{created['id']}", json={"timeout_ms": 3000}, headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["timeout_ms"] == 3000
    assert body["name"] == "orders"  # untouched
    assert body["upstream_url"] == created["upstream_url"]  # untouched
    assert body["updated_at"] > created["updated_at"]


async def test_patch_to_taken_name_is_409(client: AsyncClient, auth_headers: Headers) -> None:
    await create(client, auth_headers, name="orders")
    users = (await create(client, auth_headers, name="users")).json()
    r = await client.patch(f"{URL}/{users['id']}", json={"name": "orders"}, headers=auth_headers)
    assert r.status_code == 409


async def test_patch_explicit_null_is_422(client: AsyncClient, auth_headers: Headers) -> None:
    created = (await create(client, auth_headers)).json()
    r = await client.patch(f"{URL}/{created['id']}", json={"name": None}, headers=auth_headers)
    assert r.status_code == 422


async def test_patch_unknown_is_404(client: AsyncClient, auth_headers: Headers) -> None:
    r = await client.patch(f"{URL}/{uuid.uuid4()}", json={"timeout_ms": 3000}, headers=auth_headers)
    assert r.status_code == 404


# ---- Delete


async def test_delete_then_gone(client: AsyncClient, auth_headers: Headers) -> None:
    created = (await create(client, auth_headers)).json()
    r = await client.delete(f"{URL}/{created['id']}", headers=auth_headers)
    assert r.status_code == 204
    assert r.content == b""
    assert (await client.get(f"{URL}/{created['id']}", headers=auth_headers)).status_code == 404


async def test_delete_unknown_is_404(client: AsyncClient, auth_headers: Headers) -> None:
    r = await client.delete(f"{URL}/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


async def test_delete_with_routes_is_409_until_routes_are_gone(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    service = await make_service(db, workspace.id)
    route = await make_route(db, workspace.id, service.id)
    path = f"{URL}/{service.id}"

    r = await client.delete(path, headers=auth_headers)
    assert r.status_code == 409
    assert r.json() == {"detail": "Service still has routes. Delete its routes first."}
    assert (await client.get(path, headers=auth_headers)).status_code == 200  # still there

    await db.delete(route)
    await db.commit()
    assert (await client.delete(path, headers=auth_headers)).status_code == 204


# ---- Auth


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", URL),
        ("POST", URL),
        ("GET", f"{URL}/{uuid.uuid4()}"),
        ("PATCH", f"{URL}/{uuid.uuid4()}"),
        ("DELETE", f"{URL}/{uuid.uuid4()}"),
    ],
)
async def test_every_endpoint_needs_a_token(client: AsyncClient, method: str, path: str) -> None:
    r = await client.request(method, path, json={})
    assert r.status_code == 401


# ---- Workspace isolation (option C)


@pytest.fixture
async def other_workspace_service(db: AsyncSession, other_workspace: Workspace) -> Service:
    """A service that belongs to a different workspace than the test admin."""
    return await make_service(db, other_workspace.id, name="orders")


async def test_other_workspace_is_invisible(
    client: AsyncClient, auth_headers: Headers, other_workspace_service: Service
) -> None:
    path = f"{URL}/{other_workspace_service.id}"

    listing = (await client.get(URL, headers=auth_headers)).json()
    assert listing == {"items": [], "total": 0}
    assert (await client.get(path, headers=auth_headers)).status_code == 404
    assert (
        await client.patch(path, json={"timeout_ms": 1000}, headers=auth_headers)
    ).status_code == 404
    assert (await client.delete(path, headers=auth_headers)).status_code == 404


async def test_same_name_allowed_in_different_workspaces(
    client: AsyncClient, auth_headers: Headers, other_workspace_service: Service
) -> None:
    # "orders" exists in the other workspace; ours may still use it.
    r = await create(client, auth_headers, name="orders")
    assert r.status_code == 201


# ---- Robustness


async def test_parallel_creates_with_same_name(client: AsyncClient, auth_headers: Headers) -> None:
    # Two requests at the same moment: the database's unique constraint
    # guarantees exactly one wins, with no duplicate row and no 500 error.
    results = await asyncio.gather(*(create(client, auth_headers) for _ in range(2)))
    assert sorted(r.status_code for r in results) == [201, 409]

    listing = (await client.get(URL, headers=auth_headers)).json()
    assert listing["total"] == 1


async def test_other_integrity_errors_are_not_reported_as_409(db: AsyncSession) -> None:
    # A missing workspace breaks the foreign key, not the name rule. That is a
    # bug, so it must surface as an error instead of a misleading "name taken".
    data = ServiceCreate(name="orders", upstream_url="http://x:1")
    with pytest.raises(IntegrityError):
        await crud.create_service(
            db, workspace_id=uuid.uuid4(), data=data, audit=AuditContext.system()
        )
