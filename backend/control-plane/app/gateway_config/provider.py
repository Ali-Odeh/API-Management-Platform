"""Answers "is there a newer config than mine?", waiting if needed (long polling)."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine

from app.gateway_config import crud
from app.gateway_config.notifier import ConfigNotifier
from app.gateway_config.schemas import ConfigSnapshot


class ConfigProvider:
    def __init__(self, engine: AsyncEngine, notifier: ConfigNotifier) -> None:
        self._engine = engine
        self._notifier = notifier
        self._cached: ConfigSnapshot | None = None
        self._build_lock = asyncio.Lock()

    async def get_if_changed(self, known_version: int | None, wait: float) -> ConfigSnapshot | None:
        """A snapshot if the config differs from `known_version`, else None (-> 304).

        With wait > 0 and nothing new yet, hold on until a change is announced
        or `wait` seconds pass. Either way, the version is then re-read from the
        database: a NOTIFY only wakes us up, the database decides the answer.
        """
        wake = self._notifier.subscribe()  # before reading the version, see notifier.py
        version = await crud.read_version(self._engine)
        if version == known_version and wait > 0:
            await self._notifier.wait(wake, seconds=wait)
            version = await crud.read_version(self._engine)
        if version == known_version:
            return None
        return await self._snapshot(version)

    async def _snapshot(self, version: int) -> ConfigSnapshot:
        """Build each version's snapshot once, even if many gateways ask at the same moment.

        When a change wakes 10 waiting gateways together, the first one builds
        the snapshot and the other 9 reuse it (no "thundering herd" on Postgres).
        """
        if self._cached is not None and self._cached.version >= version:
            return self._cached
        async with self._build_lock:
            if self._cached is None or self._cached.version < version:
                self._cached = await crud.read_snapshot(self._engine)
            return self._cached
