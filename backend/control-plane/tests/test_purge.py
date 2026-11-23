"""The workspace purge job (app/workspaces/purge.py), run as its own login."""

import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, pool, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.audit.models import AuditEvent
from app.core.db import Base, SessionLocal
from app.core.permissions import Role
from app.gateway_config.models import WorkspaceConfig
from app.workspaces.models import Workspace, WorkspaceStatus
from app.workspaces.purge import KEPT_TABLES, TENANT_TABLES, purge_due, run
from tests.conftest import OWNER_URL
from tests.factories import (
    make_api,
    make_api_key,
    make_consumer,
    make_member,
    make_route,
    make_service,
    make_user,
    make_workspace,
)

PURGE_URL = os.environ["PURGE_DATABASE_URL"]


async def _populated(db: AsyncSession, slug: str) -> Workspace:
    """A workspace with one of everything, an event in its own log and a request logged."""
    workspace = await make_workspace(db, slug=slug)
    service = await make_service(db, workspace.id)
    route = await make_route(db, workspace.id, service.id)
    await make_api(db, workspace.id, route_ids=(route.id,))
    consumer = await make_consumer(db, workspace.id)
    await make_api_key(db, workspace.id, consumer.id)
    person = await make_user(db, email=f"owner@{slug}.test")
    await make_member(db, workspace.id, person.id, Role.OWNER)
    owner = create_async_engine(OWNER_URL, poolclass=pool.NullPool)
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_events (id, workspace_id, actor_type, action, target_type, "
                "changes) VALUES (gen_random_uuid(), :ws, 'user', 'service.create', 'service', "
                "'{}')"
            ),
            {"ws": workspace.id},
        )
        # The worker inserts these as apim_ingest; the month's partition
        # comes from the same function it calls.
        await conn.execute(text("SELECT ensure_request_log_partitions(ARRAY[current_date])"))
        await conn.execute(
            text(
                "INSERT INTO request_logs (event_id, ts, workspace_id, method, path, status, "
                "latency_ms, upstream_ms, bytes) VALUES (gen_random_uuid(), now(), :ws, 'GET', "
                "'/v1/orders', 200, 1, 0, 0)"
            ),
            {"ws": workspace.id},
        )
    await owner.dispose()
    return workspace


async def _delete(workspace_id: uuid.UUID, *, purge_in: timedelta) -> None:
    """Mark it deleted, with its purge due `purge_in` from now (negative: overdue)."""
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        await session.execute(
            update(Workspace)
            .where(Workspace.id == workspace_id)
            .values(
                status=WorkspaceStatus.DELETED,
                deleted_at=now + purge_in - timedelta(days=30),
                purge_after=now + purge_in,
            )
        )
        await session.commit()


async def _rows(workspace_id: uuid.UUID) -> dict[str, int]:
    """How many rows each tenant table holds for the workspace."""
    async with SessionLocal() as session:
        return {
            table: await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE workspace_id = :ws"), {"ws": workspace_id}
            )
            or 0
            for table in TENANT_TABLES
        }


async def _status(workspace_id: uuid.UUID) -> WorkspaceStatus:
    async with SessionLocal() as session:
        return (await session.get_one(Workspace, workspace_id)).status


def test_the_purge_knows_every_table_with_workspace_data() -> None:
    # A new tenant table must be purged too: a purge that left rows behind
    # would never fail on its own (the workspace row stays, so no FK complains).
    with_workspace = {
        table.name
        for table in Base.metadata.sorted_tables
        if "workspace_id" in table.columns and table.name not in KEPT_TABLES
    }
    assert set(TENANT_TABLES) == with_workspace


async def test_purges_only_overdue_deleted_workspaces(db: AsyncSession) -> None:
    due = await _populated(db, "due")
    not_yet = await _populated(db, "not-yet")
    active = await _populated(db, "active")
    await _delete(due.id, purge_in=-timedelta(seconds=1))
    await _delete(not_yet.id, purge_in=timedelta(days=1))

    assert await purge_due(PURGE_URL) == ["due"]

    assert set((await _rows(due.id)).values()) == {0}
    assert await _status(due.id) == WorkspaceStatus.PURGED
    assert await _status(not_yet.id) == WorkspaceStatus.DELETED
    for untouched in (not_yet, active):
        assert set((await _rows(untouched.id)).values()) == {1}
    # People stay: only the membership went.
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Workspace)) == 3


async def test_gateways_learn_of_the_purge(db: AsyncSession) -> None:
    # The workspace's config row stays, with a newer sequence: a gateway that
    # was away when it was deleted still gets it in `deleted` (MT.6).
    due = await _populated(db, "due")
    await _delete(due.id, purge_in=-timedelta(seconds=1))
    async with SessionLocal() as session:
        before = await session.scalar(
            select(WorkspaceConfig.seq).where(WorkspaceConfig.workspace_id == due.id)
        )

    await purge_due(PURGE_URL)

    async with SessionLocal() as session:
        after = await session.scalar(
            select(WorkspaceConfig.seq).where(WorkspaceConfig.workspace_id == due.id)
        )
    assert before is not None and after is not None and after > before


async def test_the_purge_is_recorded_platform_level(db: AsyncSession) -> None:
    due = await _populated(db, "due")
    await _delete(due.id, purge_in=-timedelta(seconds=1))

    await purge_due(PURGE_URL)

    async with SessionLocal() as session:
        [event] = await session.scalars(
            select(AuditEvent).where(AuditEvent.action == "workspace.purged")
        )
    # It survives the purge of the workspace's own log.
    assert (event.workspace_id, event.target_id, event.target_name) == (None, due.id, "due")
    assert event.actor_type == "system"
    assert event.changes["status"] == ["deleted", "purged"]
    assert event.changes["services"] == [1, 0]
    assert event.changes["audit_events"] == [1, 0]


async def test_nothing_due_is_a_quiet_round(db: AsyncSession) -> None:
    await _populated(db, "active")
    assert await purge_due(PURGE_URL) == []
    assert await purge_due(PURGE_URL) == []  # and a second round too


async def test_a_workspace_being_restored_is_left_for_the_next_round(db: AsyncSession) -> None:
    # Restore holds the row lock; the job skips it instead of waiting.
    due = await _populated(db, "due")
    await _delete(due.id, purge_in=-timedelta(seconds=1))
    async with SessionLocal() as session:
        await session.execute(select(Workspace).where(Workspace.id == due.id).with_for_update())

        assert await purge_due(PURGE_URL) == []

    assert await _status(due.id) == WorkspaceStatus.DELETED


async def test_run_repeats_until_stopped(
    db: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    due = await _populated(db, "due")
    await _delete(due.id, purge_in=-timedelta(seconds=1))
    stop = asyncio.Event()
    caplog.set_level(logging.INFO, logger="workspace_purge")

    job = asyncio.create_task(run(PURGE_URL, every=0.01, stop=stop))
    await asyncio.sleep(0.2)  # several rounds
    stop.set()
    await asyncio.wait_for(job, timeout=2)  # stops promptly

    assert await _status(due.id) == WorkspaceStatus.PURGED
    rounds = [r for r in caplog.records if "purged 0 workspaces" in r.getMessage()]
    assert len(rounds) >= 1


async def test_a_failed_round_is_retried_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    unreachable = "postgresql+asyncpg://apim_purge:x@127.0.0.1:1/apim"
    stop = asyncio.Event()

    job = asyncio.create_task(run(unreachable, every=0.01, stop=stop))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(job, timeout=2)

    failures = [r for r in caplog.records if "workspace purge failed" in r.getMessage()]
    assert len(failures) >= 2  # it kept trying
