"""Guard tests: rules checked on EVERY endpoint, discovered from the app itself.

They protect endpoints that don't exist yet: add a route next month, forget the
lock, and these fail without anyone writing a new test.
"""

import re
import uuid
from typing import Any

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient

from app.core.config import get_settings
from app.main import app

Endpoint = tuple[str, str]  # (method, path template)

PUBLIC: set[Endpoint] = {("GET", "/health"), ("POST", "/api/v1/auth/login")}


def _all_endpoints() -> list[Endpoint]:
    return sorted(
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    )


ALL = _all_endpoints()
ADMIN = [e for e in ALL if e[1].startswith("/api/v1/") and e not in PUBLIC]
INTERNAL = [e for e in ALL if e[1].startswith("/internal/")]


def _url(path: str) -> str:
    """'/api/v1/services/{service_id}' -> '/api/v1/services/<random uuid>'."""
    return re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)


def _operation(method: str, path: str) -> dict[str, Any]:
    operation: dict[str, Any] = app.openapi()["paths"][path][method.lower()]
    return operation


def test_discovery_finds_the_endpoints() -> None:
    # If discovery broke, the parametrized tests below would silently test nothing.
    assert len(ADMIN) >= 17
    assert ("GET", "/internal/config") in INTERNAL


def test_every_endpoint_is_public_admin_or_internal() -> None:
    # A new URL prefix forces a conscious decision about how it is protected.
    unclassified = [e for e in ALL if e not in PUBLIC and e not in ADMIN and e not in INTERNAL]
    assert unclassified == []


# ---- Admin endpoints need an admin JWT


@pytest.mark.parametrize(("method", "path"), ADMIN)
async def test_admin_endpoint_rejects_missing_token(
    client: AsyncClient, method: str, path: str
) -> None:
    r = await client.request(method, _url(path), json={})
    assert r.status_code == 401


@pytest.mark.parametrize(("method", "path"), ADMIN)
async def test_admin_endpoint_rejects_the_internal_token(
    client: AsyncClient, method: str, path: str
) -> None:
    headers = {"X-Internal-Token": get_settings().internal_token}
    r = await client.request(method, _url(path), json={}, headers=headers)
    assert r.status_code == 401


# ---- Internal endpoints need the internal token


@pytest.mark.parametrize(("method", "path"), INTERNAL)
async def test_internal_endpoint_rejects_missing_token(
    gateway: AsyncClient, method: str, path: str
) -> None:
    r = await gateway.request(method, _url(path), headers={"X-Internal-Token": ""})
    assert r.status_code == 401


@pytest.mark.parametrize(("method", "path"), INTERNAL)
async def test_internal_endpoint_rejects_an_admin_jwt(
    client: AsyncClient, auth_headers: dict[str, str], method: str, path: str
) -> None:
    r = await client.request(method, _url(path), headers=auth_headers)
    assert r.status_code == 401


# ---- The docs (Swagger) tell the truth


@pytest.mark.parametrize(("method", "path"), ADMIN + INTERNAL)
def test_protected_endpoints_document_401(method: str, path: str) -> None:
    assert "401" in _operation(method, path)["responses"]


@pytest.mark.parametrize(("method", "path"), [e for e in ADMIN if "{" in e[1]])
def test_endpoints_with_an_id_document_404(method: str, path: str) -> None:
    assert "404" in _operation(method, path)["responses"]


@pytest.mark.parametrize(("method", "path"), ALL)
def test_every_endpoint_has_a_tag(method: str, path: str) -> None:
    assert _operation(method, path).get("tags"), "group it in Swagger with tags=[...]"
