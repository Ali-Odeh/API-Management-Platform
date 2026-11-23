"""Purging deleted workspaces once their restore period is over (decision 10).

Runs in its own container as `apim_purge`, the only login that may delete a
workspace's whole data and its audit events (the API's own login can't
erase audit events). For each workspace whose `purge_after` has passed it
deletes, in one transaction, everything that belongs to the workspace
(children before parents: the foreign keys to `workspaces` RESTRICT), then
keeps the row as `purged`, so the slug is never given to anyone again, and
records a platform-level `workspace.purged` event that outlives the purge.

Imports nothing from the API (no settings, no engine), like the retention
job: it needs one URL.
"""

import asyncio
import json
import logging

import asyncpg

from app.core.jobs import asyncpg_dsn, repeat

logger = logging.getLogger("workspace_purge")

# Every table holding a workspace's data, children first. The guard test
# checks it against every model with a workspace_id, so a new table can't be
# forgotten (a purge that left rows behind would never fail on its own).
TENANT_TABLES = (
    "request_logs",  # no foreign keys: first or last, either works
    "api_keys",
    "apps",  # before the APIs they were made for (their consumers cascade them too)
    "api_routes",  # before the APIs and routes they link
    "apis",
    "routes",
    "service_targets",
    "services",
    "consumers",
    "plans",  # after the consumers on them
    "workspace_members",
    "audit_events",
)
# Kept on purpose: the workspace row (its slug stays taken) and its config
# row, whose sequence tells a gateway that was away that the workspace is gone.
KEPT_TABLES = ("workspaces", "workspace_config")

# SKIP LOCKED: a workspace being restored right now (its row locked) is left
# for the next round, when it is no longer due.
_NEXT_DUE = """
    SELECT id, slug FROM workspaces
     WHERE status = 'deleted' AND purge_after <= now()
     ORDER BY purge_after
     LIMIT 1
       FOR UPDATE SKIP LOCKED
"""
_MARK_PURGED = "UPDATE workspaces SET status = 'purged' WHERE id = $1"
_RECORD = """
    INSERT INTO audit_events (id, actor_type, action, target_type, target_id, target_name, changes)
    VALUES (gen_random_uuid(), 'system', 'workspace.purged', 'workspace', $1, $2, $3::jsonb)
"""


async def purge_due(database_url: str) -> list[str]:
    """Purge every workspace that is due; returns their slugs, in purge order."""
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        purged: list[str] = []
        while (slug := await _purge_next(conn)) is not None:
            purged.append(slug)
        return purged
    finally:
        await conn.close()


async def _purge_next(conn: asyncpg.Connection) -> str | None:
    async with conn.transaction():
        workspace = await conn.fetchrow(_NEXT_DUE)
        if workspace is None:
            return None
        changes: dict[str, list[object]] = {"status": ["deleted", "purged"]}
        for table in TENANT_TABLES:
            status = await conn.execute(
                f"DELETE FROM {table} WHERE workspace_id = $1", workspace["id"]
            )
            changes[table] = [int(status.split()[-1]), 0]  # "DELETE 12": how many went
        await conn.execute(_MARK_PURGED, workspace["id"])
        await conn.execute(_RECORD, workspace["id"], workspace["slug"], json.dumps(changes))
    logger.info("purged workspace %s (%s)", workspace["slug"], workspace["id"])
    slug: str = workspace["slug"]
    return slug


async def run(database_url: str, *, every: float, stop: asyncio.Event) -> None:
    """Purge what is due now and then every `every` seconds, until `stop` is set."""

    async def job_round() -> None:
        purged = await purge_due(database_url)
        logger.info("purged %d workspaces", len(purged))

    await repeat(job_round, every=every, stop=stop, logger=logger, name="workspace purge")
