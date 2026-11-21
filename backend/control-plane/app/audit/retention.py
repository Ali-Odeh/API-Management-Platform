"""Deleting audit events older than the retention period (decision 11).

Runs in its own container as `apim_retention`, the only login that may
delete audit events; the API itself can only add and read them. Imports
nothing from the API (no settings, no engine): it needs one URL and a number.
"""

import asyncio
import contextlib
import logging

import asyncpg
from sqlalchemy import make_url

logger = logging.getLogger("audit_retention")

DEFAULT_DAYS = 365
DAY_SECONDS = 24 * 60 * 60
# Deleting a year's worth at once would hold locks for long; batches don't.
_BATCH = 5000
_DELETE_BATCH = """
    DELETE FROM audit_events
     WHERE id IN (
        SELECT id FROM audit_events
         WHERE created_at < now() - make_interval(days => $1)
         LIMIT $2
     )
"""


def _dsn(database_url: str) -> str:
    """'postgresql+asyncpg://...' -> 'postgresql://...', the form asyncpg takes."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


async def delete_expired(database_url: str, *, days: int, batch: int = _BATCH) -> int:
    """Delete every event older than `days`; returns how many."""
    if days < 1:
        raise ValueError("The retention period must be at least one day")
    conn = await asyncpg.connect(_dsn(database_url))
    try:
        deleted = 0
        while True:
            status = await conn.execute(_DELETE_BATCH, days, batch)  # e.g. "DELETE 5000"
            count = int(status.split()[-1])
            deleted += count
            if count < batch:
                return deleted
    finally:
        await conn.close()


async def run(database_url: str, *, days: int, every: float, stop: asyncio.Event) -> None:
    """Delete expired events now and then every `every` seconds, until `stop` is set.

    A failed round (e.g. the database restarting) is logged and retried at
    the next one: old events can wait a day, the job must not die.
    """
    while not stop.is_set():
        try:
            deleted = await delete_expired(database_url, days=days)
            logger.info("deleted %d audit events older than %d days", deleted, days)
        except (OSError, asyncpg.PostgresError):
            logger.exception("audit retention failed; trying again at the next round")
        with contextlib.suppress(TimeoutError):  # the wait ends early when stopped
            await asyncio.wait_for(stop.wait(), timeout=every)
