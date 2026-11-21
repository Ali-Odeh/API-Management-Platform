"""Guard tests: rules checked on EVERY endpoint, discovered from the app itself.

They protect endpoints that don't exist yet: add a route next month, forget the
lock, and these fail without anyone writing a new test.

- Every signed-in endpoint is about the caller, a platform endpoint, or acts
  on a workspace through `require_permission(...)`.
- Permission guard: every role gets exactly what the permission map promises.
- Isolation: a member of workspace A can never reach workspace B's objects.
"""

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import PermissionCheck, require_platform_admin
from app.core.permissions import Permission, Role, role_allows
from app.keys.models import ApiKey
from app.main import app
from app.users.models import User
from app.workspaces.crud import WORKSPACE_NOT_FOUND
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
SELF: set[Endpoint] = {("GET", "/api/v1/me"), ("GET", "/api/v1/me/workspaces")}
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
    # and everything under /workspaces/{slug} must check one.
    under_slug = {e for e in SIGNED_IN if e[1].startswith(WORKSPACE_PREFIX)}
    assert under_slug == set(WORKSPACE)


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


def _headers(user: User, *, mfa: bool = False) -> dict[str, str]:
    assert user.auth_user_id is not None
    return bearer(make_token(user.auth_user_id, user.email, aal="aal2" if mfa else "aal1"))


def _passed_the_check(r: Any) -> bool:
    """The permission check let the request through (what follows may still say
    404 for an unknown id, or 422 for an empty body)."""
    detail = r.json().get("detail") if r.headers.get("content-type") == "application/json" else None
    return r.status_code not in (401, 403) and detail != WORKSPACE_NOT_FOUND


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("method", "path"), WORKSPACE_ENDPOINTS)
async def test_each_role_gets_what_the_permission_map_promises(
    client: AsyncClient, db: AsyncSession, method: str, path: str, role: Role
) -> None:
    workspace = await make_workspace(db, slug="acme")
    member = await make_user(db, email=f"{role}@acme.test")
    await make_member(db, workspace.id, member.id, role)

    r = await client.request(method, _url(path, "acme"), json={}, headers=_headers(member))

    if role_allows(role, WORKSPACE[(method, path)]):
        assert _passed_the_check(r), r.text
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
    client: AsyncClient, db: AsyncSession, method: str, path: str
) -> None:
    # Platform power reaches every tenant: without MFA it stops; with it, it passes.
    await make_workspace(db, slug="acme")
    admin = await make_user(db, email="platform@example.com", is_platform_admin=True)

    r = await client.request(method, _url(path, "acme"), json={}, headers=_headers(admin))
    assert r.status_code == 403
    assert r.json() == {"detail": "mfa_required"}

    r = await client.request(method, _url(path, "acme"), json={}, headers=_headers(admin, mfa=True))
    assert _passed_the_check(r), r.text


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
    theirs = {
        "service_id": service.id,
        "route_id": route.id,
        "consumer_id": consumer.id,
        "key_id": key.id,
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
    known = {"service_id", "route_id", "consumer_id", "key_id"}
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
