"""Listens for Postgres `NOTIFY config_changed` and wakes waiting long-poll requests.

One instance per control-plane process, started and stopped by the app's lifespan.
It holds its OWN database connection (not one from the pool), because a
LISTEN only works while that exact connection stays open.

How a waiting request uses it, without ever missing a change:

    wake = notifier.subscribe()        # 1. take the wake-up signal FIRST
    seq = await read_seq()             # 2. then read the sequence
    if seq == since:
        await notifier.wait(wake, seconds=30)  # 3. until a NOTIFY or 30 s

If a NOTIFY lands between steps 1 and 2, the signal from step 1 is already
set, so step 3 returns at once instead of sleeping through the change.
"""

import asyncio
import contextlib
import logging
from typing import Any

import asyncpg

from app.gateway_config.models import CONFIG_CHANGED_CHANNEL

logger = logging.getLogger(__name__)


class ConfigNotifier:
    def __init__(self, dsn: str, *, reconnect_delay: float = 1.0) -> None:
        self._dsn = dsn
        self._reconnect_delay = reconnect_delay
        self._wake_signal = asyncio.Event()
        self._listening = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.backend_pid: int | None = None  # Postgres process id of our connection

    # ---- for waiting requests

    def subscribe(self) -> asyncio.Event:
        """The signal the next NOTIFY will set. Take it BEFORE reading the sequence."""
        return self._wake_signal

    async def wait(self, signal: asyncio.Event, *, seconds: float) -> None:
        """Sleep until `signal` is set or `seconds` pass (never raises on timeout)."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(signal.wait(), seconds)

    # ---- lifecycle

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="config-notifier")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._wake_all()  # let any request still waiting answer right away

    async def wait_until_listening(self, *, seconds: float = 5.0) -> None:
        await asyncio.wait_for(self._listening.wait(), seconds)

    # ---- internals

    def _wake_all(self) -> None:
        # Swap in a fresh signal for future waiters, then set the old one, which
        # wakes everybody who subscribed before this moment, all at once.
        signal, self._wake_signal = self._wake_signal, asyncio.Event()
        signal.set()

    def _on_notify(self, *_args: Any) -> None:
        self._wake_all()

    async def _run(self) -> None:
        """Stay connected and listening forever; reconnect if the connection drops."""
        while True:
            conn: asyncpg.Connection | None = None
            try:
                conn = await asyncpg.connect(self._dsn)
                closed = asyncio.Event()
                # `event=closed` binds THIS iteration's event (a plain closure would
                # see whatever `closed` refers to when it finally runs).
                conn.add_termination_listener(lambda _conn, event=closed: event.set())
                await conn.add_listener(CONFIG_CHANGED_CHANNEL, self._on_notify)
                self.backend_pid = conn.get_server_pid()
                self._listening.set()
                # Changes may have happened while we weren't listening: wake
                # everyone so they re-check the version in the database.
                self._wake_all()
                await closed.wait()
                logger.warning("config notifier: connection lost, reconnecting")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("config notifier: can't listen, retrying", exc_info=True)
            finally:
                self._listening.clear()
                if conn is not None and not conn.is_closed():
                    await conn.close()
            await asyncio.sleep(self._reconnect_delay)
