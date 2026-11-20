"""Guard tests: rules checked on EVERY endpoint, discovered from the app itself.

They protect endpoints that don't exist yet: add a route next month, forget the
lock, and these fail without anyone writing a new test.
"""

import re
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_workspace_id, require_platform_admin
from app.main import app
from tests.factories import make_user
from tests.fakes import bearer, make_token

Endpoint = tuple[str, str]  # (method, path template)

PUBLIC: set[Endpoint] = {("GET", "/health")}
# Any signed-in person may call these: they only concern the caller.
SELF: set[Endpoint] = {("GET", "/api/v1/me")}


def _routes() -> list[APIRoute]:
    return [route for route in app.routes if isinstance(route, APIRoute)]


def _all_endpoints() -> list[Endpoint]:
    return sorted((method, route.path) for route in _routes() for method in route.methods)


def _uses(dependant: Dependant, dependency: Callable[..., Any]) -> bool:
    """Does the endpoint depend on `dependency`, directly or through another one?"""
    return any(d.call is dependency or _uses(d, dependency) for d in dependant.dependencies)


def _endpoints_using(dependency: Callable[..., Any]) -> list[Endpoint]:
    return sorted(
        (method, route.path)
        for route in _routes()
        if _uses(route.dependant, dependency)
        for method in route.methods
    )


ALL = _all_endpoints()
SIGNED_IN = [e for e in ALL if e[1].startswith("/api/v1/") and e not in PUBLIC]
INTERNAL = [e for e in ALL if e[1].startswith("/internal/")]
PLATFORM = _endpoints_using(require_platform_admin)
WORKSPACE = _endpoints_using(get_workspace_id)


def _url(path: str) -> str:
    """'/api/v1/services/{service_id}' -> '/api/v1/services/<random uuid>'."""
    return re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)


def _operation(method: str, path: str) -> dict[str, Any]:
    operation: dict[str, Any] = app.openapi()["paths"][path][method.lower()]
    return operation


def test_discovery_finds_the_endpoints() -> None:
    # If discovery broke, the parametrized tests below would silently test nothing.
    assert len(SIGNED_IN) >= 18
    assert len(WORKSPACE) >= 16
    assert ("PATCH", "/api/v1/users/{user_id}") in PLATFORM
    assert ("GET", "/internal/config") in INTERNAL


def test_every_endpoint_is_public_signed_in_or_internal() -> None:
    # A new URL prefix forces a conscious decision about how it is protected.
    unclassified = [e for e in ALL if e not in PUBLIC and e not in SIGNED_IN and e not in INTERNAL]
    assert unclassified == []


def test_every_signed_in_endpoint_declares_who_may_call_it() -> None:
    # Signed in is not enough: each endpoint is either about the caller, a
    # platform endpoint, or acts on a workspace (whose rules check the caller).
    undeclared = [e for e in SIGNED_IN if e not in SELF | set(PLATFORM) | set(WORKSPACE)]
    assert undeclared == []


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


@pytest.mark.parametrize(("method", "path"), PLATFORM + WORKSPACE)
async def test_endpoint_refuses_a_person_without_rights(
    client: AsyncClient, db: AsyncSession, method: str, path: str
) -> None:
    person = await make_user(db)
    assert person.auth_user_id is not None
    headers = bearer(make_token(person.auth_user_id, person.email, aal="aal2"))
    r = await client.request(method, _url(path), json={}, headers=headers)
    assert r.status_code == 403
    assert r.json() == {"detail": "platform_admin_required"}


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
