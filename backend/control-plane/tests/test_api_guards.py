"""Guard tests: rules checked on EVERY endpoint, discovered from the app itself.

They protect endpoints that don't exist yet: add a route next month, forget the
lock, and these fail without anyone writing a new test.

- Every signed-in endpoint is about the caller, a platform endpoint, or acts
  on a workspace through `require_permission(...)`.
- Permission guard: every role gets exactly what the permission map promises.
- Isolation: a member of workspace A can never reach workspace B's objects.
"""

import json
import re
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, pool, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.audit.models import AuditEvent
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.deps import PermissionCheck, require_platform_admin
from app.core.permissions import ROLE_PERMISSIONS, Permission, Role
from app.keys.models import ApiKey
from app.main import app
from app.users.models import User
from app.workspaces.crud import WORKSPACE_NOT_FOUND
from app.workspaces.models import Workspace, WorkspaceStatus
from tests.conftest import OWNER_URL
from tests.factories import (
    make_api_key,
    make_consumer,
    make_member,
    make_route,
    make_service,
    make_user,
    make_workspace,
)
from tests.fakes import bearer, make_token

Endpoint = tuple[str, str]  # (method, path template)

PUBLIC: set[Endpoint] = {("GET", "/health")}
# Any signed-in person may call these: they only concern the caller.
SELF: set[Endpoint] = {
    ("GET", "/api/v1/me"),
    ("GET", "/api/v1/me/workspaces"),
    ("DELETE", "/api/v1/me/workspaces/{slug}"),  # leave a workspace I belong to
}
LEAVE: Endpoint = ("DELETE", "/api/v1/me/workspaces/{slug}")
DELETE_WORKSPACE: Endpoint = ("DELETE", "/api/v1/workspaces/{slug}")
LIMITS: Endpoint = ("PATCH", "/api/v1/workspaces/{slug}/limits")
WORKSPACE_PREFIX = "/api/v1/workspaces/{slug}"


def _routes() -> list[APIRoute]:
    return [route for route in app.routes if isinstance(route, APIRoute)]


def _dependencies(dependant: Dependant) -> list[Callable[..., Any]]:
    """Every dependency of an endpoint, also those of its dependencies."""
    found: list[Callable[..., Any]] = []
    for sub in dependant.dependencies:
        if sub.call is not None:
            found.append(sub.call)
        found += _dependencies(sub)
    return found


def _permissions(route: APIRoute) -> list[Permission]:
    return [d.permission for d in _dependencies(route.dependant) if isinstance(d, PermissionCheck)]


ALL = sorted((m, r.path) for r in _routes() for m in r.methods)
SIGNED_IN = [e for e in ALL if e[1].startswith("/api/v1/") and e not in PUBLIC]
INTERNAL = [e for e in ALL if e[1].startswith("/internal/")]
PLATFORM = sorted(
    (m, r.path) for r in _routes() if require_platform_admin in _dependencies(r.dependant)
    for m in r.methods
)  # fmt: skip
# Workspace endpoint -> the one permission it declares.
WORKSPACE: dict[Endpoint, Permission] = {
    (m, r.path): perms[0] for r in _routes() if (perms := _permissions(r)) for m in r.methods
}
WORKSPACE_ENDPOINTS = sorted(WORKSPACE)


def _url(path: str, slug: str = "default", ids: dict[str, uuid.UUID] | None = None) -> str:
    """Fill a path template: the slug, the given ids, a random uuid for the rest."""
    ids = ids or {}
    path = path.replace("{slug}", slug)
    return re.sub(r"\{([^}]+)\}", lambda m: str(ids.get(m.group(1), uuid.uuid4())), path)


def _operation(method: str, path: str) -> dict[str, Any]:
    operation: dict[str, Any] = app.openapi()["paths"][path][method.lower()]
    return operation


# ---- Every endpoint is classified


def test_discovery_finds_the_endpoints() -> None:
    # If discovery broke, the parametrized tests below would silently test nothing.
    assert len(SIGNED_IN) >= 22
    assert len(WORKSPACE) >= 18
    assert ("PATCH", "/api/v1/users/{user_id}") in PLATFORM
    assert ("POST", "/api/v1/workspaces") in PLATFORM
    assert ("GET", "/internal/config") in INTERNAL


def test_every_endpoint_is_public_signed_in_or_internal() -> None:
    # A new URL prefix forces a conscious decision about how it is protected.
    unclassified = [e for e in ALL if e not in PUBLIC and e not in SIGNED_IN and e not in INTERNAL]
    assert unclassified == []


def test_every_signed_in_endpoint_declares_who_may_call_it() -> None:
    # Signed in is not enough: each endpoint is about the caller, a platform
    # endpoint, or declares a workspace permission.
    undeclared = [e for e in SIGNED_IN if e not in SELF | set(PLATFORM) | set(WORKSPACE)]
    assert undeclared == []


def test_workspace_endpoints_live_under_their_workspace() -> None:
    # A permission only means something inside the workspace named in the URL,
    # and everything under /workspaces/{slug} must check one, unless it is a
    # platform endpoint acting on the workspace itself (its lifecycle).
    under_slug = {e for e in SIGNED_IN if e[1].startswith(WORKSPACE_PREFIX)}
    assert under_slug - set(PLATFORM) == set(WORKSPACE)
    assert not set(PLATFORM) & set(WORKSPACE)


@pytest.mark.parametrize(("method", "path"), WORKSPACE_ENDPOINTS)
def test_endpoint_declares_exactly_one_fitting_permission(method: str, path: str) -> None:
    route = next(r for r in _routes() if r.path == path and method in r.methods)
    assert len(_permissions(route)) == 1
    # A write behind a read permission would let a Viewer change things.
    assert WORKSPACE[(method, path)].is_read is (method == "GET")


# ---- Signed-in endpoints need a GoTrue access token


@pytest.mark.parametrize(("method", "path"), SIGNED_IN)
async def test_signed_in_endpoint_rejects_missing_token(
    client: AsyncClient, method: str, path: str
) -> None:
    r = await client.request(method, _url(path), json={})
    assert r.status_code == 401


@pytest.mark.parametrize(("method", "path"), SIGNED_IN)
async def test_signed_in_endpoint_rejects_the_internal_token(
    client: AsyncClient, method: str, path: str
) -> None:
    headers = {"X-Internal-Token": get_settings().internal_token}
    r = await client.request(method, _url(path), json={}, headers=headers)
    assert r.status_code == 401


@pytest.mark.parametrize(("method", "path"), SIGNED_IN)
async def test_signed_in_endpoint_rejects_the_service_role_key(
    client: AsyncClient, method: str, path: str
) -> None:
    # GoTrue's admin key is signed with the same secret, but it is nobody.
    headers = bearer(get_settings().gotrue_service_role_key)
    r = await client.request(method, _url(path), json={}, headers=headers)
    assert r.status_code == 401


# ---- Platform endpoints: Platform Admin with MFA (decision 12)


@pytest.mark.parametrize(("method", "path"), PLATFORM)
async def test_platform_endpoint_needs_mfa(
    client: AsyncClient, auth_headers: dict[str, str], method: str, path: str
) -> None:
    r = await client.request(method, _url(path), json={}, headers=auth_headers)
    assert r.status_code == 403
    assert r.json() == {"detail": "mfa_required"}


@pytest.mark.parametrize(("method", "path"), PLATFORM)
async def test_platform_endpoint_refuses_everyone_else(
    client: AsyncClient, db: AsyncSession, method: str, path: str
) -> None:
    person = await make_user(db)
    r = await client.request(method, _url(path), json={}, headers=_headers(person, mfa=True))
    assert r.status_code == 403
    assert r.json() == {"detail": "platform_admin_required"}


# ---- Permission guard: every role, every workspace endpoint (decision 2)

# PLAN.md section 6, "Roles and permissions", copied by hand on purpose: the
# expectations must not come from the map under test, or a mistake there
# (say, Viewers granted a write) would be expected instead of caught.
PLAN_MATRIX: dict[Role, set[str]] = {
    Role.VIEWER: {
        "services:read", "routes:read", "consumers:read", "keys:read",
        "members:read", "workspace:read",
    },
    Role.API_ADMIN: {
        "services:read", "routes:read", "consumers:read", "keys:read",
        "members:read", "workspace:read",
        "services:write", "routes:write", "consumers:write", "keys:write", "audit:read",
    },
    Role.OWNER: {
        "services:read", "routes:read", "consumers:read", "keys:read",
        "members:read", "workspace:read",
        "services:write", "routes:write", "consumers:write", "keys:write", "audit:read",
        "members:write", "workspace:write", "workspace:delete",
    },
}  # fmt: skip


@pytest.mark.parametrize("role", list(Role))
def test_the_permission_map_is_the_plans(role: Role) -> None:
    assert {p.value for p in ROLE_PERMISSIONS[role]} == PLAN_MATRIX[role]


def test_every_permission_is_in_the_plan() -> None:
    assert {p.value for p in Permission} == set().union(*PLAN_MATRIX.values())


def _headers(user: User, *, mfa: bool = False) -> dict[str, str]:
    assert user.auth_user_id is not None
    return bearer(make_token(user.auth_user_id, user.email, aal="aal2" if mfa else "aal1"))


@pytest.fixture
async def acme(db: AsyncSession) -> dict[str, uuid.UUID]:
    """Workspace `acme` with one object of each kind: path parameter -> its id.

    The service has no routes (so it can be deleted); the route uses another one.
    `user_id` is a Viewer to change or remove; `candidate@acme.test` is a user
    who isn't a member yet, to add.
    """
    workspace = await make_workspace(db, slug="acme")
    service = await make_service(db, workspace.id, name="spare")
    routed = await make_service(db, workspace.id, name="routed")
    route = await make_route(db, workspace.id, routed.id)
    consumer = await make_consumer(db, workspace.id)
    key = await make_api_key(db, workspace.id, consumer.id)
    viewer = await make_user(db, email="target@acme.test")
    await make_member(db, workspace.id, viewer.id, Role.VIEWER)
    await make_user(db, email="candidate@acme.test")
    return {
        "workspace_id": workspace.id,
        "service_id": service.id,
        "route_id": route.id,
        "consumer_id": consumer.id,
        "key_id": key.id,
        "user_id": viewer.id,
    }


# A valid body for every workspace endpoint that takes one, so an allowed
# request must really succeed (2xx), not just get past the check.
_VALID_BODIES: dict[Endpoint, Callable[[dict[str, uuid.UUID]], dict[str, Any]]] = {
    ("POST", "/api/v1/workspaces/{slug}/services"): lambda ids: {
        "name": "new", "upstream_url": "http://mock-backend:9000",
    },
    ("PATCH", "/api/v1/workspaces/{slug}/services/{service_id}"): lambda ids: {"timeout_ms": 3000},
    ("POST", "/api/v1/workspaces/{slug}/routes"): lambda ids: {
        "service_id": str(ids["service_id"]), "path_prefix": "/v1/new", "methods": ["GET"],
    },
    ("PATCH", "/api/v1/workspaces/{slug}/routes/{route_id}"): lambda ids: {"methods": ["GET"]},
    ("POST", "/api/v1/workspaces/{slug}/consumers"): lambda ids: {
        "name": "new-app", "email": "dev@example.com",
    },
    ("PATCH", "/api/v1/workspaces/{slug}/consumers/{consumer_id}"): lambda ids: {
        "rate_limit_per_min": 100,
    },
    ("POST", "/api/v1/workspaces/{slug}/consumers/{consumer_id}/keys"): lambda ids: {"name": "k"},
    ("POST", "/api/v1/workspaces/{slug}/members"): lambda ids: {
        "email": "candidate@acme.test", "role": "viewer",
    },
    ("PATCH", "/api/v1/workspaces/{slug}/members/{user_id}"): lambda ids: {"role": "api_admin"},
    ("PATCH", "/api/v1/workspaces/{slug}"): lambda ids: {"name": "Acme Renamed"},
    ("DELETE", "/api/v1/workspaces/{slug}"): lambda ids: {"confirm": "acme"},
}  # fmt: skip


def test_every_workspace_write_has_a_valid_example() -> None:
    # A new endpoint that takes a body must show what a valid request looks like.
    with_body = {e for e in WORKSPACE_ENDPOINTS if _operation(*e).get("requestBody")}
    assert with_body == set(_VALID_BODIES)


async def _valid_request(
    client: AsyncClient, method: str, path: str, ids: dict[str, uuid.UUID], user: User, mfa: bool
) -> Any:
    body = _VALID_BODIES.get((method, path))
    return await client.request(
        method,
        _url(path, "acme", ids),
        json=body(ids) if body else None,
        headers=_headers(user, mfa=mfa),
    )


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("method", "path"), WORKSPACE_ENDPOINTS)
async def test_each_role_gets_exactly_what_the_plan_allows(
    client: AsyncClient,
    db: AsyncSession,
    acme: dict[str, uuid.UUID],
    method: str,
    path: str,
    role: Role,
) -> None:
    member = await make_user(db, email=f"{role}@acme.test")
    await make_member(db, acme["workspace_id"], member.id, role)

    r = await _valid_request(client, method, path, acme, member, mfa=False)

    if WORKSPACE[(method, path)].value in PLAN_MATRIX[role]:
        assert 200 <= r.status_code < 300, r.text
    else:
        assert r.status_code == 403
        assert r.json() == {"detail": "permission_denied"}


@pytest.mark.parametrize(("method", "path"), WORKSPACE_ENDPOINTS)
async def test_a_non_member_is_told_the_workspace_does_not_exist(
    client: AsyncClient, db: AsyncSession, method: str, path: str
) -> None:
    await make_workspace(db, slug="acme")
    stranger = await make_user(db, email="stranger@example.com")
    for slug in ("acme", "no-such-workspace"):  # the same answer: nothing is revealed
        r = await client.request(method, _url(path, slug), json={}, headers=_headers(stranger))
        assert r.status_code == 404
        assert r.json() == {"detail": WORKSPACE_NOT_FOUND}


@pytest.mark.parametrize(("method", "path"), WORKSPACE_ENDPOINTS)
async def test_platform_admin_outside_their_role_needs_mfa(
    client: AsyncClient, db: AsyncSession, acme: dict[str, uuid.UUID], method: str, path: str
) -> None:
    # Platform power reaches every tenant: without MFA it stops; with it, it works.
    admin = await make_user(db, email="platform@example.com", is_platform_admin=True)

    r = await _valid_request(client, method, path, acme, admin, mfa=False)
    assert r.status_code == 403
    assert r.json() == {"detail": "mfa_required"}

    r = await _valid_request(client, method, path, acme, admin, mfa=True)
    assert 200 <= r.status_code < 300, r.text


# ---- Isolation: workspace A can never reach workspace B's objects (decision 5)


@dataclass
class Tenants:
    alice: User  # Owner of `acme`
    acme_id: uuid.UUID
    theirs: dict[str, uuid.UUID]  # path parameter -> an object of `globex`


@pytest.fixture
async def tenants(db: AsyncSession) -> Tenants:
    acme = await make_workspace(db, slug="acme")
    globex = await make_workspace(db, slug="globex")
    alice = await make_user(db, email="alice@acme.test")
    await make_member(db, acme.id, alice.id, Role.OWNER)

    service = await make_service(db, globex.id)
    route = await make_route(db, globex.id, service.id)
    consumer = await make_consumer(db, globex.id)
    key = await make_api_key(db, globex.id, consumer.id)
    their_member = await make_user(db, email="member@globex.test")
    await make_member(db, globex.id, their_member.id, Role.VIEWER)
    theirs = {
        "service_id": service.id,
        "route_id": route.id,
        "consumer_id": consumer.id,
        "key_id": key.id,
        "user_id": their_member.id,
    }
    return Tenants(alice=alice, acme_id=acme.id, theirs=theirs)


# Bodies valid apart from the object they name, so only isolation can refuse them.
_BODIES: dict[Endpoint, dict[str, Any]] = {
    ("POST", "/api/v1/workspaces/{slug}/consumers/{consumer_id}/keys"): {"name": "k"},
}


def _path_ids(path: str) -> list[str]:
    return [p for p in re.findall(r"\{([^}]+)\}", path) if p != "slug"]


ENDPOINTS_WITH_IDS = [e for e in WORKSPACE_ENDPOINTS if _path_ids(e[1])]


def test_isolation_knows_every_kind_of_id() -> None:
    # A new kind of object in a URL must get an object of `globex` in `tenants`.
    known = {"service_id", "route_id", "consumer_id", "key_id", "user_id"}
    assert {p for e in WORKSPACE_ENDPOINTS for p in _path_ids(e[1])} <= known


@pytest.mark.parametrize(("method", "path"), ENDPOINTS_WITH_IDS)
async def test_another_workspaces_object_is_not_found(
    client: AsyncClient, db: AsyncSession, tenants: Tenants, method: str, path: str
) -> None:
    # Alice is Owner of `acme`, so only isolation can stop her here.
    url = _url(path, "acme", tenants.theirs)
    body = _BODIES.get((method, path), {})

    r = await client.request(method, url, json=body, headers=_headers(tenants.alice))

    assert r.status_code == 404, r.text
    assert r.json()["detail"] != WORKSPACE_NOT_FOUND  # the object, not the workspace
    revoked = await db.scalar(select(func.count()).where(ApiKey.revoked_at.is_not(None)))
    assert revoked == 0  # nothing of theirs changed


# Bodies that name another object, with the field to point at `globex`.
_REFERENCES: dict[Endpoint, tuple[str, dict[str, Any]]] = {
    ("POST", "/api/v1/workspaces/{slug}/routes"): (
        "service_id",
        {"path_prefix": "/v1/x", "methods": ["GET"]},
    ),
    ("PATCH", "/api/v1/workspaces/{slug}/routes/{route_id}"): ("service_id", {}),
}


def _body_id_fields(method: str, path: str) -> set[str]:
    """Fields of the request body that name another object (`..._id`)."""
    body = _operation(method, path).get("requestBody")
    if body is None:
        return set()
    ref = body["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
    properties = app.openapi()["components"]["schemas"][ref]["properties"]
    return {name for name in properties if name.endswith("_id")}


def test_isolation_knows_every_body_reference() -> None:
    # A new body field naming another object must be tested for isolation too.
    referencing = {e for e in WORKSPACE_ENDPOINTS if _body_id_fields(*e)}
    assert referencing == set(_REFERENCES)


@pytest.mark.parametrize(("method", "path"), sorted(_REFERENCES))
async def test_naming_another_workspaces_object_is_422(
    client: AsyncClient, db: AsyncSession, tenants: Tenants, method: str, path: str
) -> None:
    field, body = _REFERENCES[(method, path)]
    ids = dict(tenants.theirs)
    if "{route_id}" in path:  # point one of alice's own routes elsewhere
        service = await make_service(db, tenants.acme_id, name="own")
        ids["route_id"] = (await make_route(db, tenants.acme_id, service.id)).id
    payload = {**body, field: str(tenants.theirs[field])}

    r = await client.request(
        method, _url(path, "acme", ids), json=payload, headers=_headers(tenants.alice)
    )

    assert r.status_code == 422, r.text


# Query parameters that name another object (filters), with a request that uses one.
_QUERY_REFERENCES: dict[Endpoint, str] = {
    ("GET", "/api/v1/workspaces/{slug}/routes"): "service_id",
}


def _query_id_params(method: str, path: str) -> set[str]:
    params = _operation(method, path).get("parameters", [])
    return {p["name"] for p in params if p["in"] == "query" and p["name"].endswith("_id")}


def test_isolation_knows_every_query_reference() -> None:
    # A new filter naming another object must be tested for isolation too.
    referencing = {e for e in WORKSPACE_ENDPOINTS if _query_id_params(*e)}
    assert referencing == set(_QUERY_REFERENCES)


@pytest.mark.parametrize(("method", "path"), sorted(_QUERY_REFERENCES))
async def test_filtering_by_another_workspaces_object_is_422(
    client: AsyncClient, tenants: Tenants, method: str, path: str
) -> None:
    # Not an empty page: a reference to another workspace's object is refused
    # the same way everywhere.
    param = _QUERY_REFERENCES[(method, path)]
    r = await client.request(
        method,
        _url(path, "acme"),
        params={param: str(tenants.theirs[param])},
        headers=_headers(tenants.alice),
    )
    assert r.status_code == 422, r.text


# ---- Audit guard: every change leaves exactly one event (decision 11)

MUTATING = sorted(e for e in SIGNED_IN if e[0] in {"POST", "PATCH", "DELETE"})
GETS = sorted(e for e in SIGNED_IN if e[0] == "GET")

# The event each mutating endpoint writes. A new one must be added here, which
# is the point: an endpoint without an audit event can't go unnoticed.
_ACTIONS: dict[Endpoint, str] = {
    ("POST", "/api/v1/workspaces"): "workspace.created",
    ("PATCH", "/api/v1/users/{user_id}"): "user.disable",
    ("POST", "/api/v1/workspaces/{slug}/services"): "service.create",
    ("PATCH", "/api/v1/workspaces/{slug}/services/{service_id}"): "service.update",
    ("DELETE", "/api/v1/workspaces/{slug}/services/{service_id}"): "service.delete",
    ("POST", "/api/v1/workspaces/{slug}/routes"): "route.create",
    ("PATCH", "/api/v1/workspaces/{slug}/routes/{route_id}"): "route.update",
    ("DELETE", "/api/v1/workspaces/{slug}/routes/{route_id}"): "route.delete",
    ("POST", "/api/v1/workspaces/{slug}/consumers"): "consumer.create",
    ("PATCH", "/api/v1/workspaces/{slug}/consumers/{consumer_id}"): "consumer.update",
    ("DELETE", "/api/v1/workspaces/{slug}/consumers/{consumer_id}"): "consumer.delete",
    ("POST", "/api/v1/workspaces/{slug}/consumers/{consumer_id}/keys"): "key.create",
    ("DELETE", "/api/v1/workspaces/{slug}/keys/{key_id}"): "key.revoke",
    ("POST", "/api/v1/workspaces/{slug}/members"): "member.add",
    ("PATCH", "/api/v1/workspaces/{slug}/members/{user_id}"): "member.role_change",
    ("DELETE", "/api/v1/workspaces/{slug}/members/{user_id}"): "member.remove",
    LEAVE: "member.leave",
    ("PATCH", "/api/v1/workspaces/{slug}"): "workspace.update",
    DELETE_WORKSPACE: "workspace.deleted",
    ("POST", "/api/v1/workspaces/{slug}/suspend"): "workspace.suspended",
    ("POST", "/api/v1/workspaces/{slug}/unsuspend"): "workspace.unsuspended",
    ("POST", "/api/v1/workspaces/{slug}/restore"): "workspace.restored",
    LIMITS: "workspace.limits_change",
}
# Changes recorded in the workspace's own log. Lifecycle events (deleting it
# too) are platform-level (decision 10): they must survive the purge of that log.
# A limits change is a platform endpoint, but the workspace's Owners see it.
IN_WORKSPACE_LOG = (set(WORKSPACE) | {LEAVE, LIMITS}) - {DELETE_WORKSPACE}

# Valid requests for the platform endpoints (the workspace ones use _VALID_BODIES).
_PLATFORM_BODIES: dict[Endpoint, Callable[[User], dict[str, Any] | None]] = {
    ("POST", "/api/v1/workspaces"): lambda person: {
        "name": "Newco", "slug": "newco", "owner_email": person.email,
    },
    ("PATCH", "/api/v1/users/{user_id}"): lambda person: {"is_active": False},
    ("POST", "/api/v1/workspaces/{slug}/suspend"): lambda person: {"reason": "abuse"},
    ("POST", "/api/v1/workspaces/{slug}/unsuspend"): lambda person: None,
    ("POST", "/api/v1/workspaces/{slug}/restore"): lambda person: None,
    LIMITS: lambda person: {"max_services": 50},
}  # fmt: skip
# Where `acme` must be in its life for the change to be valid.
_STATUS_BEFORE: dict[Endpoint, WorkspaceStatus] = {
    ("POST", "/api/v1/workspaces/{slug}/unsuspend"): WorkspaceStatus.SUSPENDED,
    ("POST", "/api/v1/workspaces/{slug}/restore"): WorkspaceStatus.DELETED,
}


def test_every_mutating_endpoint_names_its_audit_event() -> None:
    assert set(_ACTIONS) == set(MUTATING)
    assert set(_PLATFORM_BODIES) == set(MUTATING) - set(WORKSPACE) - SELF


@dataclass
class Actors:
    owner: User  # Owner of `acme`
    platform_admin: User  # not a member of `acme`
    person: User  # the one platform endpoints act on
    leaver: User  # a Viewer of `acme` who leaves it


@pytest.fixture
async def actors(db: AsyncSession, acme: dict[str, uuid.UUID]) -> Actors:
    owner = await make_user(db, email="owner@acme.test")
    await make_member(db, acme["workspace_id"], owner.id, Role.OWNER)
    platform_admin = await make_user(db, email="platform@example.com", is_platform_admin=True)
    person = await make_user(db, email="person@example.com")
    leaver = await make_user(db, email="leaver@acme.test")
    await make_member(db, acme["workspace_id"], leaver.id, Role.VIEWER)
    return Actors(owner=owner, platform_admin=platform_admin, person=person, leaver=leaver)


async def _mutate(
    client: AsyncClient,
    endpoint: Endpoint,
    acme: dict[str, uuid.UUID],
    actors: Actors,
    *,
    as_platform_admin: bool = False,
) -> Any:
    """A valid request that changes something: as acme's Owner, or for platform
    endpoints (and with `as_platform_admin`) as the Platform Admin with MFA."""
    method, path = endpoint
    if endpoint in WORKSPACE:
        user = actors.platform_admin if as_platform_admin else actors.owner
        return await _valid_request(client, method, path, acme, user, mfa=as_platform_admin)
    if endpoint == LEAVE:
        return await client.request(method, _url(path, "acme"), headers=_headers(actors.leaver))
    body = _PLATFORM_BODIES[endpoint](actors.person)
    url = _url(path, "acme", ids={"user_id": actors.person.id})
    return await client.request(
        method, url, json=body, headers=_headers(actors.platform_admin, mfa=True)
    )


async def _prepare(endpoint: Endpoint, acme: dict[str, uuid.UUID]) -> None:
    """Put `acme` where the change needs it (say, deleted, to restore it)."""
    if endpoint in _STATUS_BEFORE:
        await set_status(acme["workspace_id"], _STATUS_BEFORE[endpoint])


async def _event_ids() -> set[uuid.UUID]:
    async with SessionLocal() as session:  # a fresh session: no cached rows
        return set(await session.scalars(select(AuditEvent.id)))


async def _events_since(seen: set[uuid.UUID]) -> list[AuditEvent]:
    async with SessionLocal() as session:
        events = await session.scalars(select(AuditEvent).where(AuditEvent.id.not_in(seen)))
        return list(events)


@pytest.mark.parametrize(("method", "path"), MUTATING)
async def test_every_change_writes_exactly_one_event(
    client: AsyncClient,
    db: AsyncSession,
    acme: dict[str, uuid.UUID],
    actors: Actors,
    method: str,
    path: str,
) -> None:
    await _prepare((method, path), acme)
    seen = await _event_ids()

    r = await _mutate(client, (method, path), acme, actors)

    assert 200 <= r.status_code < 300, r.text
    [event] = await _events_since(seen)
    in_workspace = (method, path) in WORKSPACE or (method, path) == LEAVE
    in_log = (method, path) in IN_WORKSPACE_LOG
    actor = (
        actors.leaver
        if (method, path) == LEAVE
        else actors.owner
        if in_workspace
        else actors.platform_admin
    )
    assert event.action == _ACTIONS[(method, path)]
    assert event.workspace_id == (acme["workspace_id"] if in_log else None)
    assert (event.actor_user_id, event.actor_email) == (actor.id, actor.email)
    assert event.actor_type == ("user" if in_workspace else "platform_admin")
    assert event.request_id == r.headers["X-Request-ID"]
    assert event.changes  # what changed, field by field
    # Never a secret: no key hash, and the raw key only ever in its one answer.
    recorded = json.dumps([event.changes, event.target_name])
    assert "key_hash" not in recorded
    hashes = (await db.scalars(select(ApiKey.key_hash))).all()
    assert not any(h in recorded for h in hashes)
    if r.status_code == 201 and "key" in r.json():
        assert r.json()["key"] not in recorded


# The table each kind of change writes to.
_TABLES = {
    "workspace": "workspaces",
    "user": "users",
    "service": "services",
    "route": "routes",
    "consumer": "consumers",
    "key": "api_keys",
    "member": "workspace_members",
}


@asynccontextmanager
async def _database_refuses_writes_to(table: str) -> AsyncIterator[None]:
    """A temporary trigger makes Postgres refuse any write to `table`."""
    owner = create_async_engine(OWNER_URL, poolclass=pool.NullPool)
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "CREATE OR REPLACE FUNCTION test_refuse() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'refused by the test'; END $$"
            )
        )
        await conn.execute(
            text(
                f"CREATE TRIGGER trg_test_refuse BEFORE INSERT OR UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION test_refuse()"
            )
        )
    try:
        yield
    finally:
        async with owner.begin() as conn:
            await conn.execute(text(f"DROP TRIGGER trg_test_refuse ON {table}"))
        await owner.dispose()


@pytest.mark.parametrize(("method", "path"), MUTATING)
async def test_a_change_that_fails_leaves_no_event(
    acme: dict[str, uuid.UUID], actors: Actors, method: str, path: str
) -> None:
    # The database refuses the change itself. Its event was staged in the same
    # transaction, so it must be gone too; an event written on its own (say,
    # committed before the change) would survive and fail this test.
    table = _TABLES[_ACTIONS[(method, path)].split(".")[0]]
    await _prepare((method, path), acme)
    seen = await _event_ids()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        _database_refuses_writes_to(table),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        r = await _mutate(client, (method, path), acme, actors)

    assert r.status_code == 500
    assert await _events_since(seen) == []


@pytest.mark.parametrize(("method", "path"), sorted(set(MUTATING) & IN_WORKSPACE_LOG - {LEAVE}))
async def test_platform_admin_changes_show_in_the_workspace_log(
    client: AsyncClient, acme: dict[str, uuid.UUID], actors: Actors, method: str, path: str
) -> None:
    # The Platform Admin isn't a member of `acme`: the workspace's Owners see
    # what they did there, marked as platform power (decision 11).
    seen = await _event_ids()

    r = await _mutate(client, (method, path), acme, actors, as_platform_admin=True)

    assert 200 <= r.status_code < 300, r.text
    [event] = await _events_since(seen)
    assert event.workspace_id == acme["workspace_id"]
    assert event.actor_type == "platform_admin"
    assert event.actor_user_id == actors.platform_admin.id


@pytest.mark.parametrize(("method", "path"), GETS)
async def test_reading_writes_no_event(
    client: AsyncClient, acme: dict[str, uuid.UUID], actors: Actors, method: str, path: str
) -> None:
    seen = await _event_ids()
    if (method, path) in WORKSPACE:
        r = await _valid_request(client, method, path, acme, actors.owner, mfa=False)
    else:
        r = await client.get(_url(path), headers=_headers(actors.platform_admin, mfa=True))
    assert r.status_code == 200, r.text
    assert await _events_since(seen) == []


# ---- Lifecycle: a suspended workspace is read-only, a deleted one is gone (decision 10)


async def set_status(workspace_id: uuid.UUID, status: WorkspaceStatus) -> None:
    """Put a workspace in a status directly, with the fields that status needs."""
    now = datetime.now(UTC)
    values: dict[str, Any] = {"status": status}
    if status == WorkspaceStatus.SUSPENDED:
        values["status_reason"] = "set by a test"
    if status.is_gone:
        values |= {"deleted_at": now, "purge_after": now + timedelta(days=30)}
    async with SessionLocal() as session:
        await session.execute(update(Workspace).where(Workspace.id == workspace_id).values(values))
        await session.commit()


@pytest.mark.parametrize(("method", "path"), WORKSPACE_ENDPOINTS)
async def test_a_suspended_workspace_is_read_only_for_its_members(
    client: AsyncClient, acme: dict[str, uuid.UUID], actors: Actors, method: str, path: str
) -> None:
    await set_status(acme["workspace_id"], WorkspaceStatus.SUSPENDED)

    r = await _valid_request(client, method, path, acme, actors.owner, mfa=False)

    if WORKSPACE[(method, path)].is_read:
        assert r.status_code == 200, r.text
    else:
        assert r.status_code == 403
        assert r.json() == {"detail": "workspace_suspended"}


@pytest.mark.parametrize(("method", "path"), [e for e in WORKSPACE_ENDPOINTS if e[0] != "GET"])
async def test_only_platform_power_writes_in_a_suspended_workspace(
    client: AsyncClient, acme: dict[str, uuid.UUID], actors: Actors, method: str, path: str
) -> None:
    # The Platform Admin can still clean up (or delete) a suspended workspace,
    # with MFA, and it is recorded as platform power.
    await set_status(acme["workspace_id"], WorkspaceStatus.SUSPENDED)
    seen = await _event_ids()

    r = await _valid_request(client, method, path, acme, actors.platform_admin, mfa=False)
    assert r.status_code == 403
    assert r.json() == {"detail": "mfa_required"}

    r = await _valid_request(client, method, path, acme, actors.platform_admin, mfa=True)
    assert 200 <= r.status_code < 300, r.text
    [event] = await _events_since(seen)
    assert event.actor_type == "platform_admin"


@pytest.mark.parametrize("status", [WorkspaceStatus.DELETED, WorkspaceStatus.PURGED])
@pytest.mark.parametrize(("method", "path"), [*WORKSPACE_ENDPOINTS, LEAVE])
async def test_a_deleted_workspace_is_gone_for_everyone(
    client: AsyncClient,
    acme: dict[str, uuid.UUID],
    actors: Actors,
    method: str,
    path: str,
    status: WorkspaceStatus,
) -> None:
    # Its members and the Platform Admin alike: only the platform endpoints
    # (restore, the list) still see it.
    await set_status(acme["workspace_id"], status)
    for user, mfa in ((actors.owner, False), (actors.platform_admin, True)):
        r = await _valid_request(client, method, path, acme, user, mfa=mfa)
        assert r.status_code == 404, r.text
        assert r.json() == {"detail": WORKSPACE_NOT_FOUND}


# ---- Internal endpoints need the internal token


@pytest.mark.parametrize(("method", "path"), INTERNAL)
async def test_internal_endpoint_rejects_missing_token(
    gateway: AsyncClient, method: str, path: str
) -> None:
    r = await gateway.request(method, _url(path), headers={"X-Internal-Token": ""})
    assert r.status_code == 401


@pytest.mark.parametrize(("method", "path"), INTERNAL)
async def test_internal_endpoint_rejects_a_user_jwt(
    client: AsyncClient, mfa_headers: dict[str, str], method: str, path: str
) -> None:
    r = await client.request(method, _url(path), headers=mfa_headers)
    assert r.status_code == 401


# ---- The docs (Swagger) tell the truth


@pytest.mark.parametrize(("method", "path"), SIGNED_IN + INTERNAL)
def test_protected_endpoints_document_401(method: str, path: str) -> None:
    assert "401" in _operation(method, path)["responses"]


@pytest.mark.parametrize(("method", "path"), SIGNED_IN)
def test_signed_in_endpoints_document_403(method: str, path: str) -> None:
    assert "403" in _operation(method, path)["responses"]


@pytest.mark.parametrize(("method", "path"), [e for e in SIGNED_IN if "{" in e[1]])
def test_endpoints_with_an_id_document_404(method: str, path: str) -> None:
    assert "404" in _operation(method, path)["responses"]


@pytest.mark.parametrize(("method", "path"), ALL)
def test_every_endpoint_has_a_tag(method: str, path: str) -> None:
    assert _operation(method, path).get("tags"), "group it in Swagger with tags=[...]"
