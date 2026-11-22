"""Routes: database rules and the /api/v1/routes API."""

import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.models import Service
from app.workspaces.models import Workspace
from tests.factories import expect_violation, make_route, make_service

URL = "/api/v1/workspaces/default/routes"
Headers = dict[str, str]


@pytest.fixture
async def service(db: AsyncSession, workspace: Workspace) -> Service:
    return await make_service(db, workspace.id)


# =====================================================================
# Database rules
# =====================================================================


async def test_defaults_are_secure(
    db: AsyncSession, workspace: Workspace, service: Service
) -> None:
    route = await make_route(db, workspace.id, service.id, methods=["GET"])
    await db.refresh(route)
    assert route.methods == ["GET"]
    assert route.auth_required is True  # needs an API key unless turned off
    assert route.strip_prefix is False
    assert route.is_active is True


async def test_root_prefix_is_allowed(
    db: AsyncSession, workspace: Workspace, service: Service
) -> None:
    await make_route(db, workspace.id, service.id, path_prefix="/")


async def test_path_prefix_unique_per_workspace(
    db: AsyncSession, workspace: Workspace, other_workspace: Workspace, service: Service
) -> None:
    # Two workspaces may both publish /v1/orders (the gateway tells them apart
    # by Host from MT.7); one workspace can't have it twice.
    theirs = await make_service(db, other_workspace.id)
    await make_route(db, workspace.id, service.id, path_prefix="/v1/orders")
    await make_route(db, other_workspace.id, theirs.id, path_prefix="/v1/orders")
    await expect_violation(
        db,
        "uq_routes_workspace_id_path_prefix",
        make_route(db, workspace.id, service.id, path_prefix="/v1/orders"),
    )


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"path_prefix": "v1/orders"}, "ck_routes_path_prefix_leading_slash"),
        ({"path_prefix": "/v1/orders/"}, "ck_routes_path_prefix_no_trailing_slash"),
        ({"methods": []}, "ck_routes_methods_not_empty"),
        ({"methods": ["GET", "FETCH"]}, "ck_routes_methods_known"),
        ({"max_body_bytes": 0}, "ck_routes_max_body_bytes"),
        ({"cors_methods": ["FETCH"]}, "ck_routes_cors_methods_known"),
        ({"cors_max_age": 86_401}, "ck_routes_cors_max_age"),
        (
            {"cors_origins": ["*"], "cors_credentials": True},
            "ck_routes_cors_no_wildcard_credentials",
        ),
    ],
)
async def test_check_rules(
    db: AsyncSession,
    workspace: Workspace,
    service: Service,
    fields: dict[str, object],
    constraint: str,
) -> None:
    await expect_violation(db, constraint, make_route(db, workspace.id, service.id, **fields))


async def test_route_cannot_point_to_another_workspaces_service(
    db: AsyncSession, workspace: Workspace, other_workspace: Workspace
) -> None:
    # Option C safety, enforced by the database itself.
    theirs = await make_service(db, other_workspace.id)
    await expect_violation(db, "fk_routes_service", make_route(db, workspace.id, theirs.id))


# =====================================================================
# API
# =====================================================================


async def create(
    client: AsyncClient, headers: Headers, service: Service, **overrides: Any
) -> Response:
    body = {"service_id": str(service.id), "path_prefix": "/v1/orders", "methods": ["GET"]}
    return await client.post(URL, json=body | overrides, headers=headers)


# ---- Create


async def test_create_returns_201_with_secure_defaults(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    r = await create(client, auth_headers, service)
    assert r.status_code == 201
    body = r.json()
    assert body["service_id"] == str(service.id)
    assert body["path_prefix"] == "/v1/orders"
    assert body["methods"] == ["GET"]
    assert body["auth_required"] is True
    assert body["strip_prefix"] is False
    assert body["is_active"] is True
    # MT.11: the platform's body limit (null), CORS off.
    assert body["max_body_bytes"] is None
    assert body["cors"] == {
        "origins": [],
        "methods": [],
        "headers": [],
        "credentials": False,
        "max_age": 600,
    }


@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        ("/v1/orders/", "/v1/orders"),  # trailing slash removed
        ("/", "/"),  # the root stays
        ("/V1/Orders", "/V1/Orders"),  # case kept: HTTP paths are case-sensitive
        ("/api/v1.2/user_list~x", "/api/v1.2/user_list~x"),
    ],
)
async def test_path_prefix_is_normalized(
    client: AsyncClient, auth_headers: Headers, service: Service, sent: str, stored: str
) -> None:
    r = await create(client, auth_headers, service, path_prefix=sent)
    assert r.status_code == 201
    assert r.json()["path_prefix"] == stored


@pytest.mark.parametrize(
    "path_prefix",
    ["v1/orders", "/v1//orders", "/v1/orders?x=1", "/v1/#top", "/v1 orders", "/v1/../admin", ""],
)
async def test_bad_path_prefix_is_422(
    client: AsyncClient, auth_headers: Headers, service: Service, path_prefix: str
) -> None:
    r = await create(client, auth_headers, service, path_prefix=path_prefix)
    assert r.status_code == 422


@pytest.mark.parametrize("path_prefix", ["/health", "/health/", "/health/db"])
async def test_reserved_path_prefix_is_422(
    client: AsyncClient, auth_headers: Headers, service: Service, path_prefix: str
) -> None:
    r = await create(client, auth_headers, service, path_prefix=path_prefix)
    assert r.status_code == 422
    assert "reserved by the gateway" in r.text


# Only whole segments are reserved, and paths are case-sensitive, so the
# gateway's own /health does not hide these.
@pytest.mark.parametrize("path_prefix", ["/healthy", "/api/health", "/Health"])
async def test_paths_near_reserved_ones_are_allowed(
    client: AsyncClient, auth_headers: Headers, service: Service, path_prefix: str
) -> None:
    r = await create(client, auth_headers, service, path_prefix=path_prefix)
    assert r.status_code == 201


async def test_patch_to_reserved_prefix_is_422(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    route = (await create(client, auth_headers, service, path_prefix="/v1/orders")).json()
    r = await client.patch(
        f"{URL}/{route['id']}", json={"path_prefix": "/health"}, headers=auth_headers
    )
    assert r.status_code == 422


async def test_methods_are_normalized(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    r = await create(client, auth_headers, service, methods=["get", " Post", "GET"])
    assert r.json()["methods"] == ["GET", "POST"]


@pytest.mark.parametrize("methods", [[], ["FETCH"], ["GET", "teleport"]])
async def test_bad_methods_are_422(
    client: AsyncClient, auth_headers: Headers, service: Service, methods: list[str]
) -> None:
    r = await create(client, auth_headers, service, methods=methods)
    assert r.status_code == 422


async def test_methods_are_required(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    body = {"service_id": str(service.id), "path_prefix": "/v1/orders"}
    r = await client.post(URL, json=body, headers=auth_headers)
    assert r.status_code == 422


async def test_unknown_service_is_422(client: AsyncClient, auth_headers: Headers) -> None:
    body = {"service_id": str(uuid.uuid4()), "path_prefix": "/v1/x", "methods": ["GET"]}
    r = await client.post(URL, json=body, headers=auth_headers)
    assert r.status_code == 422
    assert r.json() == {"detail": "Service not found in this workspace"}


async def test_service_of_another_workspace_is_422(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, other_workspace: Workspace
) -> None:
    theirs = await make_service(db, other_workspace.id)
    r = await create(client, auth_headers, theirs)
    assert r.status_code == 422


async def test_duplicate_path_prefix_is_409(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    await create(client, auth_headers, service)
    # "/v1/orders/" becomes "/v1/orders" after cleanup, so it is the same prefix.
    r = await create(client, auth_headers, service, path_prefix="/v1/orders/")
    assert r.status_code == 409
    assert r.json() == {"detail": "Path prefix '/v1/orders' is already in use"}


async def test_parallel_creates_with_same_prefix(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    results = await asyncio.gather(*(create(client, auth_headers, service) for _ in range(2)))
    assert sorted(r.status_code for r in results) == [201, 409]


# ---- List / get


async def test_list_newest_first_and_filter_by_service(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    orders = await make_service(db, workspace.id, name="orders")
    users = await make_service(db, workspace.id, name="users")
    await create(client, auth_headers, orders, path_prefix="/v1/orders")
    await create(client, auth_headers, users, path_prefix="/v1/users")
    await create(client, auth_headers, orders, path_prefix="/v2/orders")

    everything = (await client.get(URL, headers=auth_headers)).json()
    assert everything["total"] == 3
    prefixes = [r["path_prefix"] for r in everything["items"]]
    assert prefixes == ["/v2/orders", "/v1/users", "/v1/orders"]

    params = {"service_id": str(orders.id)}
    only_orders = (await client.get(URL, params=params, headers=auth_headers)).json()
    assert only_orders["total"] == 2
    assert {r["path_prefix"] for r in only_orders["items"]} == {"/v1/orders", "/v2/orders"}


async def test_filter_by_a_service_not_in_this_workspace_is_422(
    client: AsyncClient,
    auth_headers: Headers,
    db: AsyncSession,
    workspace: Workspace,
    other_workspace: Workspace,
) -> None:
    theirs = await make_service(db, other_workspace.id, name="theirs")
    await make_route(db, other_workspace.id, theirs.id)
    for service_id in (theirs.id, uuid.uuid4()):  # another workspace's, or none at all
        r = await client.get(URL, params={"service_id": str(service_id)}, headers=auth_headers)
        assert r.status_code == 422
        assert r.json() == {"detail": "Service not found in this workspace"}


async def test_get_one_and_unknown(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    created = (await create(client, auth_headers, service)).json()
    assert (await client.get(f"{URL}/{created['id']}", headers=auth_headers)).json() == created
    r = await client.get(f"{URL}/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404
    assert r.json() == {"detail": "Route not found"}


# ---- Update


async def test_patch_changes_only_sent_fields(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    created = (await create(client, auth_headers, service)).json()
    r = await client.patch(
        f"{URL}/{created['id']}",
        json={"methods": ["get", "delete"], "auth_required": False},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["methods"] == ["GET", "DELETE"]
    assert body["auth_required"] is False
    assert body["path_prefix"] == "/v1/orders"  # untouched


async def test_patch_moves_route_to_another_service(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    orders = await make_service(db, workspace.id, name="orders")
    v2 = await make_service(db, workspace.id, name="orders-v2")
    created = (await create(client, auth_headers, orders)).json()
    r = await client.patch(
        f"{URL}/{created['id']}", json={"service_id": str(v2.id)}, headers=auth_headers
    )
    assert r.json()["service_id"] == str(v2.id)


async def test_patch_to_unknown_service_is_422(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    created = (await create(client, auth_headers, service)).json()
    r = await client.patch(
        f"{URL}/{created['id']}", json={"service_id": str(uuid.uuid4())}, headers=auth_headers
    )
    assert r.status_code == 422


async def test_patch_to_taken_prefix_is_409(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    await create(client, auth_headers, service, path_prefix="/v1/orders")
    other = (await create(client, auth_headers, service, path_prefix="/v1/carts")).json()
    r = await client.patch(
        f"{URL}/{other['id']}", json={"path_prefix": "/v1/orders"}, headers=auth_headers
    )
    assert r.status_code == 409


@pytest.mark.parametrize("field", ["path_prefix", "methods", "service_id", "auth_required"])
async def test_patch_explicit_null_is_422(
    client: AsyncClient, auth_headers: Headers, service: Service, field: str
) -> None:
    created = (await create(client, auth_headers, service)).json()
    r = await client.patch(f"{URL}/{created['id']}", json={field: None}, headers=auth_headers)
    assert r.status_code == 422


# ---- Delete


async def test_delete_route_then_service_can_be_deleted(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    created = (await create(client, auth_headers, service)).json()
    route_path = f"{URL}/{created['id']}"
    service_path = f"/api/v1/workspaces/default/services/{service.id}"
    assert (await client.delete(service_path, headers=auth_headers)).status_code == 409

    assert (await client.delete(route_path, headers=auth_headers)).status_code == 204
    assert (await client.get(route_path, headers=auth_headers)).status_code == 404
    assert (await client.delete(service_path, headers=auth_headers)).status_code == 204


async def test_delete_unknown_is_404(client: AsyncClient, auth_headers: Headers) -> None:
    r = await client.delete(f"{URL}/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


# ---- Auth and isolation


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", URL), ("POST", URL)]
    + [(method, f"{URL}/{uuid.uuid4()}") for method in ("GET", "PATCH", "DELETE")],
)
async def test_every_endpoint_needs_a_token(client: AsyncClient, method: str, path: str) -> None:
    assert (await client.request(method, path, json={})).status_code == 401


async def test_other_workspace_routes_are_invisible(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, other_workspace: Workspace
) -> None:
    theirs = await make_service(db, other_workspace.id)
    route = await make_route(db, other_workspace.id, theirs.id, path_prefix="/their/api")
    path = f"{URL}/{route.id}"
    patch = {"is_active": False}

    assert (await client.get(URL, headers=auth_headers)).json() == {"items": [], "total": 0}
    assert (await client.get(path, headers=auth_headers)).status_code == 404
    assert (await client.patch(path, json=patch, headers=auth_headers)).status_code == 404
    assert (await client.delete(path, headers=auth_headers)).status_code == 404


# ---- Body size limit and CORS (MT.11)


async def test_body_limit_and_cors_are_saved_and_shown(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    cors = {
        "origins": [
            "HTTPS://App.Example.com:443/",
            "http://localhost:5173",
            "https://app.example.com",
        ],
        "methods": ["get", "post"],
        "headers": ["Content-Type", "X-API-Key", "content-type"],
        "credentials": True,
        "max_age": 3600,
    }
    r = await create(client, auth_headers, service, max_body_bytes=1_048_576, cors=cors)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["max_body_bytes"] == 1_048_576
    # Origins as browsers send them (lowercase, no default port, no slash), once each.
    assert body["cors"] == {
        "origins": ["https://app.example.com", "http://localhost:5173"],
        "methods": ["GET", "POST"],
        "headers": ["Content-Type", "X-API-Key"],
        "credentials": True,
        "max_age": 3600,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_body_bytes": 0},
        {"max_body_bytes": 100 * 1024**2 + 1},  # over MAX_REQUEST_BODY_CEILING
        {"cors": {"origins": ["*"], "credentials": True}},  # browsers refuse that
        {"cors": {"origins": ["*", "https://a.example.com"]}},  # '*' stands alone
        {"cors": {"origins": ["app.example.com"]}},  # no scheme
        {"cors": {"origins": ["ftp://a.example.com"]}},
        {"cors": {"origins": ["https://a.example.com/path"]}},
        {"cors": {"origins": ["https://a.example.com:99999"]}},
        {"cors": {"origins": [f"https://a{i}.example.com" for i in range(51)]}},
        {"cors": {"methods": ["FETCH"]}},
        {"cors": {"headers": ["X Bad"]}},
        {"cors": {"max_age": -1}},
        {"cors": {"max_age": 86_401}},
    ],
)
async def test_invalid_body_limit_or_cors_is_422(
    client: AsyncClient, auth_headers: Headers, service: Service, overrides: dict[str, Any]
) -> None:
    r = await create(client, auth_headers, service, **overrides)
    assert r.status_code == 422, r.text


async def test_any_origin_without_credentials_is_fine(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    r = await create(client, auth_headers, service, cors={"origins": ["*"]})
    assert r.status_code == 201, r.text
    assert r.json()["cors"]["origins"] == ["*"]


async def test_patch_cors_replaces_it_and_null_restores_the_default_limit(
    client: AsyncClient, auth_headers: Headers, service: Service
) -> None:
    created = (
        await create(
            client,
            auth_headers,
            service,
            max_body_bytes=2048,
            cors={"origins": ["https://a.example.com"], "headers": ["X-A"]},
        )
    ).json()
    path = f"{URL}/{created['id']}"

    r = await client.patch(
        path, json={"cors": {"origins": ["https://b.example.com"]}}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["cors"]["origins"] == ["https://b.example.com"]
    assert r.json()["cors"]["headers"] == []  # replaced as a whole
    assert r.json()["max_body_bytes"] == 2048  # untouched

    r = await client.patch(path, json={"max_body_bytes": None}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["max_body_bytes"] is None  # back to the platform default
    # Other fields still refuse null.
    r = await client.patch(path, json={"cors": None}, headers=auth_headers)
    assert r.status_code == 422
