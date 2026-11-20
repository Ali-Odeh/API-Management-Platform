"""config_version: bumped by database triggers on every config change, with a NOTIFY."""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import engine
from app.gateway_config.crud import get_config_version
from app.gateway_config.models import CONFIG_CHANGED_CHANNEL
from app.services.models import Service
from app.workspaces.models import Workspace
from tests.factories import (
    expect_violation,
    make_api_key,
    make_consumer,
    make_user,
    make_workspace,
)

Headers = dict[str, str]
API = "/api/v1"


class Versions:
    """Remembers the last version seen, so each step can assert bumped / unchanged."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.last = 1

    async def current(self) -> int:
        return (await get_config_version(self.db)).version

    async def assert_bumped(self) -> None:
        now = await self.current()
        assert now > self.last, "this change should have bumped config_version"
        self.last = now

    async def assert_unchanged(self) -> None:
        assert await self.current() == self.last, "this should not have bumped config_version"


@pytest.fixture
def versions(db: AsyncSession) -> Versions:
    return Versions(db)


# ---- The row itself


async def test_starts_at_one(versions: Versions) -> None:
    assert await versions.current() == 1


@pytest.mark.parametrize(
    ("row_id", "constraint"), [(1, "pk_config_version"), (2, "ck_config_version_single_row")]
)
async def test_a_second_row_is_impossible(db: AsyncSession, row_id: int, constraint: str) -> None:
    async def insert() -> None:
        await db.execute(text("INSERT INTO config_version (id) VALUES (:id)"), {"id": row_id})
        await db.commit()

    await expect_violation(db, constraint, insert())


async def test_updated_at_moves_with_the_version(db: AsyncSession, workspace: Workspace) -> None:
    before = await get_config_version(db)
    before_at = before.updated_at
    await make_consumer(db, workspace.id)
    after = await get_config_version(db)
    assert after.version == 2
    assert after.updated_at > before_at


# ---- Every write path bumps (through the real API)


async def test_service_and_route_changes_bump(
    client: AsyncClient, auth_headers: Headers, versions: Versions
) -> None:
    service = (
        await client.post(
            f"{API}/services",
            json={"name": "orders", "upstream_url": "http://mock-backend:9000"},
            headers=auth_headers,
        )
    ).json()
    await versions.assert_bumped()

    route = (
        await client.post(
            f"{API}/routes",
            json={"service_id": service["id"], "path_prefix": "/v1/orders", "methods": ["GET"]},
            headers=auth_headers,
        )
    ).json()
    await versions.assert_bumped()

    await client.patch(
        f"{API}/routes/{route['id']}", json={"methods": ["GET", "POST"]}, headers=auth_headers
    )
    await versions.assert_bumped()

    await client.patch(
        f"{API}/services/{service['id']}", json={"timeout_ms": 3000}, headers=auth_headers
    )
    await versions.assert_bumped()

    await client.delete(f"{API}/routes/{route['id']}", headers=auth_headers)
    await versions.assert_bumped()

    await client.delete(f"{API}/services/{service['id']}", headers=auth_headers)
    await versions.assert_bumped()


async def test_consumer_and_key_changes_bump(
    client: AsyncClient, auth_headers: Headers, versions: Versions
) -> None:
    consumer = (
        await client.post(
            f"{API}/consumers",
            json={"name": "mobile-app", "email": "dev@example.com"},
            headers=auth_headers,
        )
    ).json()
    await versions.assert_bumped()

    key = (
        await client.post(
            f"{API}/consumers/{consumer['id']}/keys", json={"name": "k"}, headers=auth_headers
        )
    ).json()
    await versions.assert_bumped()

    await client.patch(
        f"{API}/consumers/{consumer['id']}", json={"status": "blocked"}, headers=auth_headers
    )
    await versions.assert_bumped()

    await client.delete(f"{API}/keys/{key['id']}", headers=auth_headers)
    await versions.assert_bumped()

    # Revoking again writes nothing, so nothing changes for the gateway.
    await client.delete(f"{API}/keys/{key['id']}", headers=auth_headers)
    await versions.assert_unchanged()

    await client.post(
        f"{API}/consumers/{consumer['id']}/keys", json={"name": "k2"}, headers=auth_headers
    )
    await versions.assert_bumped()

    # Deleting the consumer also deletes its keys inside Postgres (cascade):
    # the triggers see that too.
    await client.delete(f"{API}/consumers/{consumer['id']}", headers=auth_headers)
    await versions.assert_bumped()


# ---- Things that must NOT bump


async def test_failed_writes_do_not_bump(
    client: AsyncClient,
    auth_headers: Headers,
    db: AsyncSession,
    workspace: Workspace,
    versions: Versions,
) -> None:
    body = {"name": "orders", "upstream_url": "http://mock-backend:9000"}
    service = (await client.post(f"{API}/services", json=body, headers=auth_headers)).json()
    await client.post(
        f"{API}/routes",
        json={"service_id": service["id"], "path_prefix": "/v1/orders", "methods": ["GET"]},
        headers=auth_headers,
    )
    versions.last = await versions.current()

    duplicate = await client.post(f"{API}/services", json=body, headers=auth_headers)
    assert duplicate.status_code == 409  # rolled back...
    await versions.assert_unchanged()  # ...so the bump was rolled back too

    has_routes = await client.delete(f"{API}/services/{service['id']}", headers=auth_headers)
    assert has_routes.status_code == 409
    await versions.assert_unchanged()

    consumer = await make_consumer(db, workspace.id)
    for _ in range(10):
        await make_api_key(db, workspace.id, consumer.id)
    versions.last = await versions.current()
    too_many = await client.post(
        f"{API}/consumers/{consumer.id}/keys", json={"name": "k"}, headers=auth_headers
    )
    assert too_many.status_code == 409
    await versions.assert_unchanged()


async def test_reads_and_login_do_not_bump(
    client: AsyncClient, auth_headers: Headers, versions: Versions
) -> None:
    for path in ("/services", "/routes", "/consumers", "/me"):
        assert (await client.get(f"{API}{path}", headers=auth_headers)).status_code == 200
    await versions.assert_unchanged()


async def test_users_and_workspaces_do_not_bump(
    db: AsyncSession, workspace: Workspace, versions: Versions
) -> None:
    await make_workspace(db, slug="another")
    await make_user(db, email="second@test.local")
    await versions.assert_unchanged()


# ---- NOTIFY: how step 2.5 will learn about changes instantly


async def test_commit_sends_notify_and_rollback_does_not(
    db: AsyncSession, workspace: Workspace
) -> None:
    received: asyncio.Queue[str] = asyncio.Queue()
    # Keep the plain id: the rollback below expires loaded objects, and reading
    # workspace.id afterwards would need a hidden reload (MissingGreenlet).
    workspace_id = workspace.id

    def on_notify(*args: Any) -> None:  # asyncpg calls (connection, pid, channel, payload)
        received.put_nowait(args[3])

    async with engine.connect() as conn:
        listener = (await conn.get_raw_connection()).driver_connection
        await listener.add_listener(CONFIG_CHANGED_CHANNEL, on_notify)

        # A change that is rolled back: the trigger ran, but NOTIFY is only
        # delivered on commit, so nobody hears about it.
        db.add(Service(workspace_id=workspace_id, name="ghost", upstream_url="http://x:1"))
        await db.flush()
        await db.rollback()

        # A committed change: one message carrying the new version.
        await make_consumer(db, workspace_id)
        payload = await asyncio.wait_for(received.get(), timeout=5)
        assert payload == "2"  # 1 -> 2; the rolled-back bump never happened
        assert received.empty()

        await listener.remove_listener(CONFIG_CHANGED_CHANNEL, on_notify)
