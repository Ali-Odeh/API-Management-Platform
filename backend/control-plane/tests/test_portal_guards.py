"""Guard tests for the developer portal (P.1, P.2), over every portal endpoint.

Discovered from the app, like test_api_guards.py, so a new portal endpoint
is covered without anyone writing a test for it:
- a blocked developer gets 403 developer_blocked everywhere;
- a developer never reaches another developer's apps or keys (404);
- every portal write records exactly one event, in the app's workspace
  log, by the developer; a write that fails records none; a read none.
Which tokens open the portal at all is in test_api_guards.py.
"""

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.apis.models import ApiStatus
from app.audit.models import AuditEvent
from app.developers.models import Developer, DeveloperStatus
from app.main import app
from tests.factories import (
    make_api,
    make_api_key,
    make_app,
    make_developer,
    make_plan,
    make_workspace,
)
from tests.fakes import bearer, make_developer_token
from tests.test_api_guards import PORTAL, _database_refuses_writes_to, _event_ids, _events_since

Endpoint = tuple[str, str]
MUTATING = sorted(e for e in PORTAL if e[0] in {"POST", "PATCH", "DELETE"})
READS = sorted(e for e in PORTAL if e[0] == "GET")


def _headers(developer: Developer) -> dict[str, str]:
    return bearer(make_developer_token(developer.auth_user_id, developer.email))


@dataclass
class World:
    mine: Developer
    theirs: Developer
    ids: dict[str, uuid.UUID]  # path parameter -> an object of `mine`
    their_ids: dict[str, uuid.UUID]  # path parameter -> an object of `theirs`
    workspace_id: uuid.UUID


@pytest.fixture
async def world(db: AsyncSession) -> World:
    """Workspace `shop` with a published API and an offered plan; two
    developers, each with an approved app and a key in it."""
    workspace = await make_workspace(db, slug="shop")
    api = await make_api(db, workspace.id, status=ApiStatus.PUBLISHED)
    plan = await make_plan(db, workspace.id, name="Free", portal_visible=True)
    mine = await make_developer(db, email="mine@portal.test")
    theirs = await make_developer(db, email="theirs@portal.test")
    ids: dict[str, dict[str, uuid.UUID]] = {}
    for developer in (mine, theirs):
        made = await make_app(db, workspace.id, developer_id=developer.id, api_id=api.id)
        key = await make_api_key(db, workspace.id, made.consumer_id)
        ids[developer.email] = {"app_id": made.id, "key_id": key.id}
    return World(
        mine=mine,
        theirs=theirs,
        ids=ids[mine.email] | {"api_id": api.id, "plan_id": plan.id},
        their_ids=ids[theirs.email],
        workspace_id=workspace.id,
    )


def _url(path: str, ids: dict[str, uuid.UUID]) -> str:
    path = path.replace("{slug}", "shop")
    return re.sub(r"\{([^}]+)\}", lambda m: str(ids.get(m.group(1), uuid.uuid4())), path)


# A valid body for every portal write that takes one.
_BODIES: dict[Endpoint, Callable[[dict[str, uuid.UUID]], dict[str, Any]]] = {
    ("POST", "/api/v1/portal/apps"): lambda ids: {
        "workspace": "shop", "api_id": str(ids["api_id"]), "plan_id": str(ids["plan_id"]),
        "name": "Another app",
    },
    ("POST", "/api/v1/portal/apps/{app_id}/keys"): lambda ids: {"name": "k"},
}  # fmt: skip
# The event each portal write records.
_ACTIONS: dict[Endpoint, str] = {
    ("POST", "/api/v1/portal/apps"): "app.create",
    ("DELETE", "/api/v1/portal/apps/{app_id}"): "app.delete",
    ("POST", "/api/v1/portal/apps/{app_id}/keys"): "key.create",
    ("DELETE", "/api/v1/portal/apps/{app_id}/keys/{key_id}"): "key.revoke",
}
_TABLES = {"app": "apps", "key": "api_keys"}


async def _request(client: AsyncClient, endpoint: Endpoint, world: World) -> Any:
    method, path = endpoint
    body = _BODIES.get(endpoint)
    return await client.request(
        method,
        _url(path, world.ids),
        json=body(world.ids) if body else None,
        headers=_headers(world.mine),
    )


def test_discovery_finds_the_portal() -> None:
    assert len(PORTAL) >= 12
    assert ("POST", "/api/v1/portal/apps") in MUTATING


def test_every_portal_write_names_its_event_and_has_a_valid_example() -> None:
    assert set(_ACTIONS) == set(MUTATING)
    with_body = {
        e for e in MUTATING if app.openapi()["paths"][e[1]][e[0].lower()].get("requestBody")
    }
    assert with_body == set(_BODIES)


@pytest.mark.parametrize(("method", "path"), PORTAL)
async def test_a_blocked_developer_is_refused_everywhere(
    client: AsyncClient, world: World, db: AsyncSession, method: str, path: str
) -> None:
    world.mine.status = DeveloperStatus.BLOCKED
    db.add(world.mine)
    await db.commit()
    r = await _request(client, (method, path), world)
    assert r.status_code == 403
    assert r.json() == {"detail": "developer_blocked"}


@pytest.mark.parametrize(("method", "path"), [e for e in PORTAL if "{app_id}" in e[1]])
async def test_another_developers_app_is_not_found(
    client: AsyncClient, world: World, method: str, path: str
) -> None:
    # Their app, in a workspace both may use: only ownership can stop this.
    ids = world.ids | {"app_id": world.their_ids["app_id"]}
    body = _BODIES.get((method, path))
    r = await client.request(
        method, _url(path, ids), json=body(ids) if body else None, headers=_headers(world.mine)
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "App not found"}


@pytest.mark.parametrize(("method", "path"), [e for e in PORTAL if "{key_id}" in e[1]])
async def test_another_apps_key_is_not_found_through_mine(
    client: AsyncClient, world: World, db: AsyncSession, method: str, path: str
) -> None:
    ids = world.ids | {"key_id": world.their_ids["key_id"]}
    seen = await _event_ids()
    r = await client.request(method, _url(path, ids), headers=_headers(world.mine))
    assert r.status_code == 404
    assert await _events_since(seen) == []


def test_the_portal_knows_every_kind_of_id() -> None:
    found = {p for _, path in PORTAL for p in re.findall(r"\{([^}]+)\}", path)}
    assert found <= {"slug", "app_id", "key_id", "api_id"}


@pytest.mark.parametrize(("method", "path"), MUTATING)
async def test_every_portal_write_records_exactly_one_event(
    client: AsyncClient, world: World, method: str, path: str
) -> None:
    seen = await _event_ids()

    r = await _request(client, (method, path), world)

    assert 200 <= r.status_code < 300, r.text
    [event] = await _events_since(seen)
    assert event.action == _ACTIONS[(method, path)]
    assert event.workspace_id == world.workspace_id  # in the workspace's own log
    assert (event.actor_type, event.actor_user_id, event.actor_email) == (
        "developer",
        world.mine.id,
        world.mine.email,
    )
    assert event.request_id == r.headers["X-Request-ID"]
    assert event.changes
    if r.status_code == 201 and "key" in r.json():
        assert r.json()["key"] not in str(event.changes)


@pytest.mark.parametrize(("method", "path"), MUTATING)
async def test_a_portal_write_that_fails_leaves_no_event(
    world: World, method: str, path: str
) -> None:
    table = _TABLES[_ACTIONS[(method, path)].split(".")[0]]
    seen = await _event_ids()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        _database_refuses_writes_to(table),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        r = await _request(client, (method, path), world)
    assert r.status_code == 500
    assert await _events_since(seen) == []


@pytest.mark.parametrize(("method", "path"), READS)
async def test_reading_the_portal_records_nothing(
    client: AsyncClient, world: World, db: AsyncSession, method: str, path: str
) -> None:
    before = await db.scalar(select(func.count()).select_from(AuditEvent))
    r = await _request(client, (method, path), world)
    assert r.status_code == 200, r.text
    assert await db.scalar(select(func.count()).select_from(AuditEvent)) == before
