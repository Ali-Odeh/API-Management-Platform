"""API tests for /api/v1/workspaces/{slug}/services and their targets."""

import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.context import AuditContext
from app.core.config import get_settings
from app.services import crud
from app.services.models import Service, ServiceTarget
from app.services.schemas import MAX_TARGETS_PER_SERVICE, ServiceCreate, TargetCreate
from app.workspaces.models import Workspace
from tests.factories import make_route, make_service, make_target

URL = "/api/v1/workspaces/default/services"
Headers = dict[str, str]


async def create(
    client: AsyncClient,
    headers: Headers,
    *,
    upstream_url: str = "http://mock-backend:9000",
    **overrides: Any,
) -> Response:
    """A service with one target at upstream_url, unless `targets` is given."""
    body = {"name": "orders", "targets": [{"url": upstream_url}]} | overrides
    return await client.post(URL, json=body, headers=headers)


def targets_url(service_id: Any) -> str:
    return f"{URL}/{service_id}/targets"


# ---- Create


async def test_create_returns_201_with_defaults(client: AsyncClient, auth_headers: Headers) -> None:
    r = await create(client, auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "orders"
    assert body["timeout_ms"] == 5000  # plan default
    assert body["is_active"] is True
    uuid.UUID(body["id"])  # a real uuid
    assert body["created_at"] and body["updated_at"]
    # Load balancing and health checks: the plan's defaults (decision 14).
    assert {k: body[k] for k in HEALTH_FIELDS} == {
        "lb_algorithm": "round_robin",
        "hc_path": "/health",
        "hc_interval_s": 10,
        "hc_unhealthy_threshold": 3,
        "hc_healthy_threshold": 2,
        "passive_failures": 5,
        "passive_eject_s": 30,
    }
    [target] = body["targets"]
    assert (target["url"], target["weight"], target["is_active"]) == (
        "http://mock-backend:9000",
        100,
        True,
    )
    uuid.UUID(target["id"])


HEALTH_FIELDS = (
    "lb_algorithm",
    "hc_path",
    "hc_interval_s",
    "hc_unhealthy_threshold",
    "hc_healthy_threshold",
    "passive_failures",
    "passive_eject_s",
)


async def test_create_with_several_targets_and_own_settings(
    client: AsyncClient, auth_headers: Headers
) -> None:
    targets = [
        {"url": "http://mock-backend:9000", "weight": 100},
        {"url": "http://mock-backend-2:9000", "weight": 50, "is_active": False},
    ]
    r = await create(
        client, auth_headers, targets=targets, hc_path="/ready", hc_interval_s=5, passive_eject_s=60
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert [(t["url"], t["weight"], t["is_active"]) for t in body["targets"]] == [
        ("http://mock-backend:9000", 100, True),
        ("http://mock-backend-2:9000", 50, False),
    ]
    assert (body["hc_path"], body["hc_interval_s"], body["passive_eject_s"]) == ("/ready", 5, 60)


async def test_create_strips_trailing_slash(client: AsyncClient, auth_headers: Headers) -> None:
    r = await create(client, auth_headers, upstream_url="https://api.example.com/v2/")
    assert r.json()["targets"][0]["url"] == "https://api.example.com/v2"


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
        {"upstream_url": "http://user:s3cret@x:9000"},  # credentials
        {"upstream_url": "http://user@x:9000"},  # a username alone
        {"timeout_ms": 99},
        {"timeout_ms": 60_001},
        {"targets": []},  # at least one
        {"targets": [{"url": f"http://b{i}:1"} for i in range(51)]},  # at most 50
        {"targets": [{"url": "http://a:1"}, {"url": "http://a:1/"}]},  # the same URL twice
        {"targets": [{"url": "http://a:1", "weight": 0}]},
        {"targets": [{"url": "http://a:1", "weight": 1001}]},
        {"lb_algorithm": "random"},
        {"hc_path": "health"},  # no leading slash
        {"hc_path": "/health?x=1"},  # a path only
        {"hc_interval_s": 0},
        {"hc_interval_s": 301},
        {"hc_unhealthy_threshold": 0},
        {"hc_healthy_threshold": 11},
        {"passive_failures": 0},
        {"passive_eject_s": 3601},
    ],
)
async def test_create_invalid_input_is_422(
    client: AsyncClient, auth_headers: Headers, overrides: dict[str, Any]
) -> None:
    r = await create(client, auth_headers, **overrides)
    assert r.status_code == 422


# ---- SSRF (decision 3): plainly internal upstreams are refused on save


@pytest.mark.parametrize(
    "upstream_url",
    [
        "http://127.0.0.1:9000",
        "http://2130706433",  # 127.0.0.1 as one number
        "http://0x7f.0.0.1",
        "http://[::1]:8000",
        "http://[::ffff:127.0.0.1]",
        "http://169.254.169.254/latest",  # cloud metadata
        "http://10.1.2.3",
        "http://localhost:5432",
        "https://LOCALHOST.",
        "http://admin.localhost",
        "http://metadata.google.internal",
    ],
)
async def test_internal_upstreams_are_refused(
    client: AsyncClient, auth_headers: Headers, upstream_url: str
) -> None:
    r = await create(client, auth_headers, upstream_url=upstream_url)
    assert r.status_code == 422
    assert "must not point inside the platform" in r.text
    assert "UPSTREAM_ALLOW" in r.text


async def test_internal_targets_are_refused_when_added(
    client: AsyncClient, auth_headers: Headers
) -> None:
    created = (await create(client, auth_headers)).json()
    r = await client.post(
        targets_url(created["id"]), json={"url": "http://10.0.0.5"}, headers=auth_headers
    )
    assert r.status_code == 422
    assert "10.0.0.5 is an internal address" in r.text


async def test_names_are_left_to_the_gateway(client: AsyncClient, auth_headers: Headers) -> None:
    # A name that may resolve inside the platform is the gateway's to refuse,
    # at connect time; here it can't be known.
    r = await create(client, auth_headers, upstream_url="http://redis:6379")
    assert r.status_code == 201


async def test_the_platform_admin_can_allow_an_internal_upstream(
    client: AsyncClient, auth_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "upstream_allow", "10.20.0.0/16,billing.internal")

    for name, upstream_url in [("a", "http://10.20.0.9:8080"), ("b", "http://billing.internal")]:
        r = await create(client, auth_headers, name=name, upstream_url=upstream_url)
        assert r.status_code == 201, r.text
    r = await create(client, auth_headers, name="c", upstream_url="http://10.21.0.9")
    assert r.status_code == 422


async def test_credentials_in_a_target_url_are_refused(
    client: AsyncClient, auth_headers: Headers
) -> None:
    created = (await create(client, auth_headers)).json()
    r = await client.post(
        targets_url(created["id"]),
        json={"url": "http://user:s3cret@x:9000"},
        headers=auth_headers,
    )
    assert r.status_code == 422
    assert "must not contain a username or password" in r.text


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
    assert body["targets"] == created["targets"]  # untouched
    assert body["updated_at"] > created["updated_at"]


async def test_patch_health_settings(client: AsyncClient, auth_headers: Headers) -> None:
    created = (await create(client, auth_headers)).json()
    r = await client.patch(
        f"{URL}/{created['id']}",
        json={"hc_path": "/status", "hc_unhealthy_threshold": 5, "passive_failures": 10},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert (body["hc_path"], body["hc_unhealthy_threshold"], body["passive_failures"]) == (
        "/status",
        5,
        10,
    )
    assert body["hc_interval_s"] == 10  # untouched


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
        ("GET", targets_url(uuid.uuid4())),
        ("POST", targets_url(uuid.uuid4())),
        ("PATCH", f"{targets_url(uuid.uuid4())}/{uuid.uuid4()}"),
        ("DELETE", f"{targets_url(uuid.uuid4())}/{uuid.uuid4()}"),
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
    data = ServiceCreate(name="orders", targets=[TargetCreate(url="http://x:1")])
    with pytest.raises(IntegrityError):
        await crud.create_service(
            db, workspace_id=uuid.uuid4(), data=data, audit=AuditContext.system()
        )


async def test_deleting_a_service_deletes_its_targets(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    service = await make_service(db, workspace.id)
    await make_target(db, workspace.id, service.id)
    assert (await client.delete(f"{URL}/{service.id}", headers=auth_headers)).status_code == 204
    count = await db.scalar(
        select(func.count())
        .select_from(ServiceTarget)
        .where(ServiceTarget.service_id == service.id)
    )
    assert count == 0


# ---- Targets (decision 14)


async def test_add_list_update_and_remove_targets(
    client: AsyncClient, auth_headers: Headers
) -> None:
    service = (await create(client, auth_headers)).json()
    first = service["targets"][0]
    url = targets_url(service["id"])

    r = await client.post(
        url, json={"url": "http://mock-backend-2:9000/", "weight": 50}, headers=auth_headers
    )
    assert r.status_code == 201, r.text
    second = r.json()
    assert (second["url"], second["weight"], second["is_active"]) == (
        "http://mock-backend-2:9000",
        50,
        True,
    )

    listing = (await client.get(url, headers=auth_headers)).json()
    assert listing["total"] == 2
    assert [t["id"] for t in listing["items"]] == [first["id"], second["id"]]  # oldest first

    r = await client.patch(
        f"{url}/{second['id']}", json={"weight": 200, "is_active": False}, headers=auth_headers
    )
    assert r.status_code == 200
    assert (r.json()["weight"], r.json()["is_active"]) == (200, False)
    assert r.json()["url"] == "http://mock-backend-2:9000"  # untouched

    assert (await client.delete(f"{url}/{second['id']}", headers=auth_headers)).status_code == 204
    after = (await client.get(f"{URL}/{service['id']}", headers=auth_headers)).json()
    assert [t["id"] for t in after["targets"]] == [first["id"]]


async def test_a_target_url_is_unique_per_service(
    client: AsyncClient, auth_headers: Headers
) -> None:
    service = (await create(client, auth_headers)).json()
    r = await client.post(
        targets_url(service["id"]), json={"url": "http://mock-backend:9000/"}, headers=auth_headers
    )
    assert r.status_code == 409
    assert r.json() == {"detail": "This service already has a target with that URL"}
    # Another service may use the same URL.
    r = await create(client, auth_headers, name="other")
    assert r.status_code == 201


async def test_the_last_target_stays(client: AsyncClient, auth_headers: Headers) -> None:
    service = (await create(client, auth_headers)).json()
    [only] = service["targets"]
    r = await client.delete(f"{targets_url(service['id'])}/{only['id']}", headers=auth_headers)
    assert r.status_code == 409
    assert "keeps at least one target" in r.json()["detail"]
    # Switching it off is allowed: the service then has no routable target.
    r = await client.patch(
        f"{targets_url(service['id'])}/{only['id']}",
        json={"is_active": False},
        headers=auth_headers,
    )
    assert r.status_code == 200


async def test_at_most_50_targets(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    service = await make_service(db, workspace.id)
    for i in range(MAX_TARGETS_PER_SERVICE - 1):
        await make_target(db, workspace.id, service.id, url=f"http://b{i}:1")
    r = await client.post(
        targets_url(service.id), json={"url": "http://one-too-many:1"}, headers=auth_headers
    )
    assert r.status_code == 409
    assert "at most 50 targets" in r.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [{"weight": 0}, {"weight": 1001}, {"weight": None}, {"is_active": None}],
)
async def test_invalid_target_update_is_422(
    client: AsyncClient, auth_headers: Headers, body: dict[str, Any]
) -> None:
    service = (await create(client, auth_headers)).json()
    target = service["targets"][0]
    r = await client.patch(
        f"{targets_url(service['id'])}/{target['id']}", json=body, headers=auth_headers
    )
    assert r.status_code == 422


async def test_unknown_targets_are_404(client: AsyncClient, auth_headers: Headers) -> None:
    service = (await create(client, auth_headers)).json()
    other = (await create(client, auth_headers, name="other")).json()
    target_of_other = other["targets"][0]["id"]
    for method in ("PATCH", "DELETE"):
        for path in (
            f"{targets_url(service['id'])}/{uuid.uuid4()}",
            # A real target, but of another service: not found under this one.
            f"{targets_url(service['id'])}/{target_of_other}",
        ):
            r = await client.request(method, path, json={"weight": 5}, headers=auth_headers)
            assert r.status_code == 404, (method, path)
            assert r.json() == {"detail": "Target not found"}
    for method in ("GET", "POST"):
        r = await client.request(
            method, targets_url(uuid.uuid4()), json={"url": "http://a:1"}, headers=auth_headers
        )
        assert r.status_code == 404
        assert r.json() == {"detail": "Service not found"}


async def test_another_workspaces_targets_are_invisible(
    client: AsyncClient, auth_headers: Headers, other_workspace_service: Service
) -> None:
    service_url = targets_url(other_workspace_service.id)
    target_id = other_workspace_service.targets[0].id
    assert (await client.get(service_url, headers=auth_headers)).status_code == 404
    r = await client.post(service_url, json={"url": "http://a:1"}, headers=auth_headers)
    assert r.status_code == 404
    r = await client.patch(f"{service_url}/{target_id}", json={"weight": 5}, headers=auth_headers)
    assert r.status_code == 404
    assert (
        await client.delete(f"{service_url}/{target_id}", headers=auth_headers)
    ).status_code == 404


async def test_parallel_deletes_keep_one_target(client: AsyncClient, auth_headers: Headers) -> None:
    # Two targets removed at the same moment: the service row lock makes the
    # second wait and count again, so one target always stays.
    service = (
        await create(
            client,
            auth_headers,
            targets=[{"url": "http://a:1"}, {"url": "http://b:1"}],
        )
    ).json()
    paths = [f"{targets_url(service['id'])}/{t['id']}" for t in service["targets"]]
    results = await asyncio.gather(*(client.delete(p, headers=auth_headers) for p in paths))
    assert sorted(r.status_code for r in results) == [204, 409]
    left = (await client.get(targets_url(service["id"]), headers=auth_headers)).json()
    assert left["total"] == 1
