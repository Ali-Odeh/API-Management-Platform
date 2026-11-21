"""Deleting audit events older than the retention period (decision 11).

Runs in its own container as `apim_retention`, the only login that may
delete audit events; the API itself can only add and read them. Imports
nothing from the API (no settings, no engine): it needs one URL and a number.
"""

import asyncio
import logging

import asyncpg

from app.core.jobs import asyncpg_dsn, repeat

logger = logging.getLogger("audit_retention")

DEFAULT_DAYS = 365
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


async def delete_expired(database_url: str, *, days: int, batch: int = _BATCH) -> int:
    """Delete every event older than `days`; returns how many."""
    if days < 1:
        raise ValueError("The retention period must be at least one day")
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
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
    """Delete expired events now and then every `every` seconds, until `stop` is set."""

    async def job_round() -> None:
        deleted = await delete_expired(database_url, days=days)
        logger.info("deleted %d audit events older than %d days", deleted, days)

    await repeat(job_round, every=every, stop=stop, logger=logger, name="audit retention")
