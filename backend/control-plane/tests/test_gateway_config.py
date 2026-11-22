"""config_seq and workspace_config: bumped by database triggers, per workspace (MT.6).

Every change to the gateway's config adds 1 to the global sequence and to the
changed workspace's version (and records the sequence on it), with a NOTIFY.
Only the workspaces a statement touched move; changes the gateway doesn't
use move nothing.
"""

import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.db import Base, SessionLocal, engine
from app.core.permissions import Role
from app.gateway_config.crud import get_config_seq
from app.gateway_config.models import CONFIG_CHANGED_CHANNEL, CONFIG_COLUMNS, WorkspaceConfig
from app.services.models import Service
from app.workspaces.models import Workspace
from tests.conftest import OWNER_URL
from tests.factories import (
    expect_violation,
    make_api_key,
    make_consumer,
    make_member,
    make_route,
    make_service,
    make_user,
    make_workspace,
)

Headers = dict[str, str]
API = "/api/v1"
Versions = dict[uuid.UUID, tuple[int, int]]  # workspace -> (version, seq)


async def _seq() -> int:
    async with SessionLocal() as session:
        return (await get_config_seq(session)).seq


async def _versions() -> Versions:
    async with SessionLocal() as session:
        rows = await session.execute(select(WorkspaceConfig))
        return {c.workspace_id: (c.version, c.seq) for c in rows.scalars()}


class Watch:
    """Remembers the sequence and the versions, so each step can assert what moved."""

    def __init__(self) -> None:
        self.seq = 0
        self.versions: Versions = {}

    async def start(self) -> "Watch":
        self.seq, self.versions = await _seq(), await _versions()
        return self

    async def assert_bumped(self, *workspaces: uuid.UUID) -> None:
        """The sequence moved by one, and exactly these workspaces moved with it."""
        seq, versions = await _seq(), await _versions()
        assert seq == self.seq + 1, "this change should have bumped config_seq once"
        moved = {ws for ws in versions if versions[ws] != self.versions.get(ws)}
        assert moved == set(workspaces)
        for ws in workspaces:
            assert versions[ws][0] == self.versions.get(ws, (0, 0))[0] + 1
            assert versions[ws][1] == seq  # the workspace records the new sequence
        self.seq, self.versions = seq, versions

    async def assert_unchanged(self) -> None:
        assert (await _seq(), await _versions()) == (self.seq, self.versions)


@pytest.fixture
async def watch(workspace: Workspace) -> Watch:
    return await Watch().start()


# ---- The tables themselves


async def test_the_sequence_starts_at_one() -> None:
    assert await _seq() == 1


@pytest.mark.parametrize(
    ("row_id", "constraint"), [(1, "pk_config_seq"), (2, "ck_config_seq_single_row")]
)
async def test_a_second_sequence_row_is_impossible(row_id: int, constraint: str) -> None:
    owner = create_async_engine(OWNER_URL)
    try:
        async with AsyncSession(owner) as session:
            insert = session.execute(
                text("INSERT INTO config_seq (id) VALUES (:id)"), {"id": row_id}
            )
            await expect_violation(session, constraint, insert)
    finally:
        await owner.dispose()


@pytest.mark.parametrize("table", ["config_seq", "workspace_config"])
async def test_the_api_can_read_but_cannot_write_config_cursors(
    db: AsyncSession, table: str
) -> None:
    permissions = await db.execute(
        text("SELECT has_table_privilege(current_user, :table, :permission)"),
        {"table": table, "permission": "SELECT"},
    )
    assert permissions.scalar_one() is True
    for permission in ("INSERT", "UPDATE", "DELETE"):
        allowed = await db.scalar(
            text("SELECT has_table_privilege(current_user, :table, :permission)"),
            {"table": table, "permission": permission},
        )
        assert allowed is False


async def test_updated_at_moves_with_the_sequence(db: AsyncSession, workspace: Workspace) -> None:
    before = (await get_config_seq(db)).updated_at
    await make_consumer(db, workspace.id)
    after = await get_config_seq(db)
    assert (after.seq, after.updated_at > before) == (3, True)  # default's insert was 2


async def test_a_new_workspace_gets_its_config_row(watch: Watch, db: AsyncSession) -> None:
    acme = await make_workspace(db, slug="acme")
    await watch.assert_bumped(acme.id)
    assert (await _versions())[acme.id][0] == 1


# ---- Every write path bumps its own workspace (through the real API)


async def test_service_and_route_changes_bump(
    client: AsyncClient, auth_headers: Headers, workspace: Workspace, watch: Watch
) -> None:
    base = f"{API}/workspaces/default"
    body = {"name": "orders", "targets": [{"url": "http://mock-backend:9000"}]}
    service = (await client.post(f"{base}/services", json=body, headers=auth_headers)).json()
    await watch.assert_bumped(workspace.id)

    route_body = {"service_id": service["id"], "path_prefix": "/v1/orders", "methods": ["GET"]}
    route = (await client.post(f"{base}/routes", json=route_body, headers=auth_headers)).json()
    await watch.assert_bumped(workspace.id)

    await client.patch(
        f"{base}/routes/{route['id']}", json={"methods": ["GET", "POST"]}, headers=auth_headers
    )
    await watch.assert_bumped(workspace.id)

    await client.patch(
        f"{base}/services/{service['id']}", json={"timeout_ms": 3000}, headers=auth_headers
    )
    await watch.assert_bumped(workspace.id)

    await client.delete(f"{base}/routes/{route['id']}", headers=auth_headers)
    await watch.assert_bumped(workspace.id)

    await client.delete(f"{base}/services/{service['id']}", headers=auth_headers)
    await watch.assert_bumped(workspace.id)


async def test_consumer_and_key_changes_bump(
    client: AsyncClient, auth_headers: Headers, workspace: Workspace, watch: Watch
) -> None:
    base = f"{API}/workspaces/default"
    body = {"name": "mobile-app", "email": "dev@example.com"}
    consumer = (await client.post(f"{base}/consumers", json=body, headers=auth_headers)).json()
    await watch.assert_bumped(workspace.id)

    keys = f"{base}/consumers/{consumer['id']}/keys"
    key = (await client.post(keys, json={"name": "k"}, headers=auth_headers)).json()
    await watch.assert_bumped(workspace.id)

    await client.patch(
        f"{base}/consumers/{consumer['id']}", json={"status": "blocked"}, headers=auth_headers
    )
    await watch.assert_bumped(workspace.id)

    await client.delete(f"{base}/keys/{key['id']}", headers=auth_headers)
    await watch.assert_bumped(workspace.id)

    # Revoking again writes nothing, so nothing changes for the gateway.
    await client.delete(f"{base}/keys/{key['id']}", headers=auth_headers)
    await watch.assert_unchanged()

    await client.post(keys, json={"name": "k2"}, headers=auth_headers)
    await watch.assert_bumped(workspace.id)


async def test_a_cascade_bumps_too(db: AsyncSession, workspace: Workspace) -> None:
    # Deleting a consumer deletes its keys inside Postgres: the keys' trigger
    # sees that statement too, so the gateway drops them.
    consumer = await make_consumer(db, workspace.id)
    await make_api_key(db, workspace.id, consumer.id)
    watch = await Watch().start()

    await db.execute(text("DELETE FROM consumers WHERE id = :id"), {"id": consumer.id})
    await db.commit()

    # Both triggers ran (consumers, and keys by the cascade), but one
    # transaction is one change: one bump, one NOTIFY (MT.10).
    await watch.assert_bumped(workspace.id)


async def test_lifecycle_and_gateway_limits_bump(
    client: AsyncClient, mfa_headers: Headers, db: AsyncSession, watch: Watch
) -> None:
    acme = await make_workspace(db, slug="acme")
    await watch.assert_bumped(acme.id)

    await client.post(f"{API}/workspaces/acme/suspend", json={"reason": "x"}, headers=mfa_headers)
    await watch.assert_bumped(acme.id)
    await client.patch(
        f"{API}/workspaces/acme/limits", json={"rate_limit_per_min": 10}, headers=mfa_headers
    )
    await watch.assert_bumped(acme.id)


# ---- Only the touched workspaces move


async def test_a_change_in_one_workspace_moves_only_that_one(
    db: AsyncSession, workspace: Workspace
) -> None:
    acme = await make_workspace(db, slug="acme")
    globex = await make_workspace(db, slug="globex")
    watch = await Watch().start()

    await make_service(db, acme.id)

    await watch.assert_bumped(acme.id)
    assert (await _versions())[globex.id] == watch.versions[globex.id]


async def test_one_statement_over_two_workspaces_bumps_once(db: AsyncSession) -> None:
    acme = await make_workspace(db, slug="acme")
    globex = await make_workspace(db, slug="globex")
    for ws in (acme, globex):
        await make_service(db, ws.id)
    watch = await Watch().start()

    await db.execute(text("UPDATE services SET timeout_ms = timeout_ms + 1"))
    await db.commit()

    await watch.assert_bumped(acme.id, globex.id)


# ---- Which columns are config: each one tried directly in SQL

# A change of each column the gateway uses (service_id, consumer_id and
# status move through the API tests above).
_CONFIG_CHANGES = [
    ("services", "timeout_ms", "timeout_ms + 1"),
    ("services", "is_active", "NOT is_active"),
    ("services", "hc_path", "'/ready'"),
    ("services", "hc_interval_s", "hc_interval_s + 1"),
    ("services", "hc_unhealthy_threshold", "hc_unhealthy_threshold + 1"),
    ("services", "hc_healthy_threshold", "hc_healthy_threshold + 1"),
    ("services", "passive_failures", "passive_failures + 1"),
    ("services", "passive_eject_s", "passive_eject_s + 1"),
    ("service_targets", "url", "'http://other:1'"),
    ("service_targets", "weight", "weight + 1"),
    ("service_targets", "is_active", "NOT is_active"),
    ("routes", "path_prefix", "'/v2/x'"),
    ("routes", "methods", "ARRAY['DELETE']"),
    ("routes", "strip_prefix", "NOT strip_prefix"),
    ("routes", "auth_required", "NOT auth_required"),
    ("routes", "is_active", "NOT is_active"),
    ("routes", "max_body_bytes", "1000"),
    ("routes", "cors_origins", "ARRAY['https://app.example.com']"),
    ("routes", "cors_methods", "ARRAY['GET']"),
    ("routes", "cors_headers", "ARRAY['X-A']"),
    ("routes", "cors_credentials", "NOT cors_credentials"),
    ("routes", "cors_max_age", "cors_max_age + 1"),
    ("consumers", "rate_limit_per_min", "7"),
    ("api_keys", "key_hash", "repeat('b', 64)"),
    ("api_keys", "revoked_at", "now()"),
    ("workspaces", "slug", "'acme-two'"),
    ("workspaces", "rate_limit_per_min", "7"),
    ("workspaces", "max_concurrent", "7"),
]
_THROUGH_THE_API = {("routes", "service_id"), ("consumers", "status"),
                    ("api_keys", "consumer_id"), ("workspaces", "status"),
                    ("service_targets", "service_id")}  # fmt: skip
# Config columns with only one allowed value so far: nothing to change them
# to yet. They are in the triggers for the day a second value arrives.
_ONE_VALUE_SO_FAR = {("services", "lb_algorithm")}
# A change of each column the gateway doesn't use.
_NOT_CONFIG = [
    ("services", "name", "'renamed'"),
    ("services", "updated_at", "now() + interval '1 hour'"),
    ("service_targets", "updated_at", "now() + interval '1 hour'"),
    ("routes", "updated_at", "now() + interval '1 hour'"),
    ("consumers", "name", "'renamed'"),
    ("consumers", "email", "'new@example.com'"),
    ("consumers", "updated_at", "now() + interval '1 hour'"),
    ("api_keys", "name", "'renamed'"),
    ("api_keys", "key_prefix", "'ak_live_zzzz'"),
    ("api_keys", "last_used_at", "now()"),
    ("api_keys", "updated_at", "now() + interval '1 hour'"),
    ("workspaces", "name", "'Renamed'"),
    ("workspaces", "max_services", "7"),
    ("workspaces", "max_routes", "7"),
    ("workspaces", "max_consumers", "7"),
    ("workspaces", "updated_at", "now() + interval '1 hour'"),
]
# Lifecycle details: they change only together with `status`, which bumps.
_WITH_STATUS = {("workspaces", c) for c in ("status_reason", "deleted_at", "purge_after")}


def test_every_column_of_a_config_table_is_decided() -> None:
    # A new column on a config table must be decided: does the gateway use
    # it? Then it belongs in CONFIG_COLUMNS (and the triggers' migration).
    for table, config in CONFIG_COLUMNS.items():
        columns = set(Base.metadata.tables[table].columns.keys()) - {
            "id",
            "workspace_id",
            "created_at",
        }
        tried = {c for t, c, _ in _CONFIG_CHANGES if t == table}
        assert set(config) == tried | {
            c for t, c in _THROUGH_THE_API | _ONE_VALUE_SO_FAR if t == table
        }, table
        others = {c for t, c, _ in _NOT_CONFIG if t == table} | {
            c for t, c in _WITH_STATUS if t == table
        }
        assert columns == set(config) | others, table


@pytest.fixture
async def one_of_each(db: AsyncSession) -> Workspace:
    acme = await make_workspace(db, slug="acme")
    service = await make_service(db, acme.id)
    await make_route(db, acme.id, service.id)
    consumer = await make_consumer(db, acme.id)
    await make_api_key(db, acme.id, consumer.id)
    return acme


@pytest.mark.parametrize(("table", "column", "value"), _CONFIG_CHANGES)
async def test_every_config_column_bumps(
    db: AsyncSession, one_of_each: Workspace, table: str, column: str, value: str
) -> None:
    watch = await Watch().start()
    await db.execute(text(f"UPDATE {table} SET {column} = {value}"))
    await db.commit()
    await watch.assert_bumped(one_of_each.id)


@pytest.mark.parametrize(("table", "column", "value"), _NOT_CONFIG)
async def test_a_change_the_gateway_does_not_use_bumps_nothing(
    db: AsyncSession, one_of_each: Workspace, table: str, column: str, value: str
) -> None:
    watch = await Watch().start()
    await db.execute(text(f"UPDATE {table} SET {column} = {value}"))
    await db.commit()
    await watch.assert_unchanged()


# ---- Things that must NOT bump


async def test_writing_the_same_values_or_no_rows_bumps_nothing(
    db: AsyncSession, one_of_each: Workspace
) -> None:
    watch = await Watch().start()
    await db.execute(text("UPDATE services SET timeout_ms = timeout_ms"))
    await db.execute(text("UPDATE routes SET is_active = false WHERE path_prefix = '/nowhere'"))
    await db.execute(text("DELETE FROM consumers WHERE name = 'nobody'"))
    await db.commit()
    await watch.assert_unchanged()


async def test_failed_writes_do_not_bump(
    client: AsyncClient, auth_headers: Headers, watch: Watch
) -> None:
    base = f"{API}/workspaces/default"
    body = {"name": "orders", "targets": [{"url": "http://mock-backend:9000"}]}
    service = (await client.post(f"{base}/services", json=body, headers=auth_headers)).json()
    await client.post(
        f"{base}/routes",
        json={"service_id": service["id"], "path_prefix": "/v1/orders", "methods": ["GET"]},
        headers=auth_headers,
    )
    await watch.start()

    duplicate = await client.post(f"{base}/services", json=body, headers=auth_headers)
    assert duplicate.status_code == 409  # rolled back...
    await watch.assert_unchanged()  # ...so the bump was rolled back too

    has_routes = await client.delete(f"{base}/services/{service['id']}", headers=auth_headers)
    assert has_routes.status_code == 409
    await watch.assert_unchanged()


async def test_reads_people_and_memberships_do_not_bump(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace, watch: Watch
) -> None:
    for path in ("/workspaces/default/services", "/me", "/me/workspaces"):
        assert (await client.get(f"{API}{path}", headers=auth_headers)).status_code == 200
    person = await make_user(db, email="second@test.local")
    await make_member(db, workspace.id, person.id, Role.VIEWER)
    await watch.assert_unchanged()


# ---- NOTIFY: how waiting gateways learn about changes instantly


async def test_commit_sends_notify_and_rollback_does_not(
    db: AsyncSession, workspace: Workspace
) -> None:
    received: asyncio.Queue[str] = asyncio.Queue()
    # Keep the plain id: the rollback below expires loaded objects, and reading
    # workspace.id afterwards would need a hidden reload (MissingGreenlet).
    workspace_id = workspace.id
    start = await _seq()

    def on_notify(*args: Any) -> None:  # asyncpg calls (connection, pid, channel, payload)
        received.put_nowait(args[3])

    async with engine.connect() as conn:
        listener = (await conn.get_raw_connection()).driver_connection
        await listener.add_listener(CONFIG_CHANGED_CHANNEL, on_notify)

        # A change that is rolled back: the trigger ran, but NOTIFY is only
        # delivered on commit, so nobody hears about it.
        db.add(Service(workspace_id=workspace_id, name="ghost"))
        await db.flush()
        await db.rollback()

        # A committed change: one message carrying the new sequence.
        await make_consumer(db, workspace_id)
        payload = await asyncio.wait_for(received.get(), timeout=5)
        assert payload == str(start + 1)  # the rolled-back bump never happened
        assert received.empty()

        await listener.remove_listener(CONFIG_CHANGED_CHANNEL, on_notify)
