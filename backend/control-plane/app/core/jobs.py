"""What the database jobs share (audit retention, workspace purge).

Each job runs in its own container with its own least-privilege login, and
imports nothing that needs the API's settings or engine: one URL is enough.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

import asyncpg
from sqlalchemy import make_url

DAY_SECONDS = 24 * 60 * 60


def asyncpg_dsn(database_url: str) -> str:
    """'postgresql+asyncpg://...' -> 'postgresql://...', the form asyncpg takes."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


async def repeat(
    job_round: Callable[[], Awaitable[None]],
    *,
    every: float,
    stop: asyncio.Event,
    logger: logging.Logger,
    name: str,
) -> None:
    """Run a round now and then every `every` seconds, until `stop` is set.

    A failed round (e.g. the database restarting) is logged and retried at
    the next one: the work can wait a day, the job must not die.
    """
    while not stop.is_set():
        try:
            await job_round()
        except (OSError, asyncpg.PostgresError):
            logger.exception("%s failed; trying again at the next round", name)
        with contextlib.suppress(TimeoutError):  # the wait ends early when stopped
            await asyncio.wait_for(stop.wait(), timeout=every)
