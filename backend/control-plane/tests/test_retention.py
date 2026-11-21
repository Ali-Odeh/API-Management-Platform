"""The audit retention job (app/audit/retention.py), run as its own login."""

import asyncio
import logging
import os
from datetime import timedelta

import pytest
from sqlalchemy import func, pool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.audit.models import AuditEvent
from app.audit.retention import delete_expired, run
from tests.conftest import OWNER_URL

RETENTION_URL = os.environ["AUDIT_RETENTION_DATABASE_URL"]


async def _events_aged(*ages: timedelta) -> None:
    """Events this old (as the owner: nobody else may backdate one)."""
    owner = create_async_engine(OWNER_URL, poolclass=pool.NullPool)
    async with owner.begin() as conn:
        for age in ages:
            await conn.execute(
                text(
                    "INSERT INTO audit_events (id, created_at, actor_type, action, target_type, "
                    "changes) VALUES (gen_random_uuid(), now() - CAST(:age AS interval), 'system', "
                    "'test', 'test', "
                    "'{}')"
                ),
                {"age": age},
            )
    await owner.dispose()


async def _count(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(AuditEvent)) or 0


async def test_deletes_only_what_is_older_than_the_period(db: AsyncSession) -> None:
    await _events_aged(timedelta(days=400), timedelta(days=366), timedelta(days=364), timedelta(0))

    assert await delete_expired(RETENTION_URL, days=365) == 2
    assert await _count(db) == 2


async def test_deletes_in_batches(db: AsyncSession) -> None:
    await _events_aged(*[timedelta(days=500)] * 7)
    assert await delete_expired(RETENTION_URL, days=365, batch=3) == 7
    assert await _count(db) == 0


async def test_the_period_is_at_least_a_day() -> None:
    with pytest.raises(ValueError, match="at least one day"):
        await delete_expired(RETENTION_URL, days=0)


async def test_run_repeats_until_stopped(
    db: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    await _events_aged(timedelta(days=500))
    stop = asyncio.Event()
    caplog.set_level(logging.INFO, logger="audit_retention")

    job = asyncio.create_task(run(RETENTION_URL, days=365, every=0.01, stop=stop))
    await asyncio.sleep(0.2)  # several rounds
    stop.set()
    await asyncio.wait_for(job, timeout=2)  # stops promptly

    assert await _count(db) == 0
    rounds = [r for r in caplog.records if "deleted" in r.getMessage()]
    assert len(rounds) >= 2


async def test_a_failed_round_is_retried_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    unreachable = "postgresql+asyncpg://apim_retention:x@127.0.0.1:1/apim"
    stop = asyncio.Event()

    job = asyncio.create_task(run(unreachable, days=365, every=0.01, stop=stop))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(job, timeout=2)

    failures = [r for r in caplog.records if "audit retention failed" in r.getMessage()]
    assert len(failures) >= 2  # it kept trying
