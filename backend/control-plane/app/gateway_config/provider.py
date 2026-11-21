"""Answers "what changed after my sequence?", waiting if needed (long polling)."""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncEngine

from app.gateway_config import crud
from app.gateway_config.notifier import ConfigNotifier
from app.gateway_config.schemas import ConfigAnswer, WorkspaceConfigOut


class ConfigProvider:
    def __init__(self, engine: AsyncEngine, notifier: ConfigNotifier) -> None:
        self._engine = engine
        self._notifier = notifier
        # Each workspace built once per version, for every gateway that asks.
        self._cache: dict[uuid.UUID, WorkspaceConfigOut] = {}
        self._build_lock = asyncio.Lock()

    async def get_changes(
        self, since: int, wait: float, slugs: frozenset[str] | None = None
    ) -> ConfigAnswer | None:
        """What changed after `since`, or None (-> 304) if nothing did.

        With wait > 0 and nothing new yet, hold on until a change is announced
        or `wait` seconds pass. Either way, the sequence is then re-read from
        the database: a NOTIFY only wakes us up, the database decides the answer.
        """
        wake = self._notifier.subscribe()  # before reading the sequence, see notifier.py
        seq = await crud.read_seq(self._engine)
        if seq == since and wait > 0:
            await self._notifier.wait(wake, seconds=wait)
            seq = await crud.read_seq(self._engine)
        if seq == since:
            return None
        return await self._answer(since, slugs)

    async def _answer(self, since: int, slugs: frozenset[str] | None) -> ConfigAnswer:
        """One answer at a time, so a workspace is built once even if a change
        wakes many waiting gateways together (no "thundering herd" on Postgres):
        the first builds it, the others find it in the cache."""
        async with self._build_lock:
            return await crud.read_answer(self._engine, since=since, slugs=slugs, cache=self._cache)
