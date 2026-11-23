"""The usage-ingest worker: the gateway's usage events into the request log (V2.1).

The gateway appends one entry per answered request to the Redis stream
`usage:events`. This worker reads them through the consumer group
`usage-ingest` and, per batch, in ONE Postgres transaction:

1. makes the month partitions the batch needs (once per month and worker),
2. inserts the rows with `ON CONFLICT DO NOTHING` (the event id makes a
   re-read entry insert once),
3. moves each used key's `last_used_at` forward (at most once a minute),

and only after the commit acknowledges the entries and deletes them from
the stream. So a worker that dies between reading and committing loses
nothing: its entries stay pending, and it re-reads them when it comes
back, or another worker claims them after CLAIM_IDLE. Several workers can
run side by side. Once a day it drops request log months older than the
retention period.

Runs in its own container as `apim_ingest` (insert request log rows, set
`last_used_at`; nothing else), like the other jobs: it imports nothing
from the API (no settings, no engine).
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import asyncpg
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from app.core.jobs import DAY_SECONDS, asyncpg_dsn
from app.usage.events import UsageEvent

logger = logging.getLogger("usage_ingest")

STREAM = "usage:events"
GROUP = "usage-ingest"
DEFAULT_RETENTION_DAYS = 90
BATCH = 500
BLOCK_MS = 1000  # how long one read waits for new entries
CLAIM_IDLE_MS = 60_000  # an entry pending this long belongs to a worker that stopped
CLAIM_EVERY = 30.0  # seconds between looks for such entries
MAX_BACKOFF = 30.0  # seconds between tries while Redis or Postgres is down
# Events this far in the future come from a broken clock; storing them would
# make partitions for months that don't exist yet.
MAX_CLOCK_SKEW = timedelta(days=1)

_INSERT = """
    INSERT INTO request_logs (
        event_id, ts, workspace_id, request_id, method, path, route_id, target_id,
        consumer_id, key_id, status, latency_ms, upstream_ms, bytes, client_ip, ratelimit
    )
    SELECT * FROM unnest(
        $1::uuid[], $2::timestamptz[], $3::uuid[], $4::text[], $5::text[], $6::text[],
        $7::uuid[], $8::uuid[], $9::uuid[], $10::uuid[], $11::smallint[], $12::float8[],
        $13::float8[], $14::bigint[], $15::inet[], $16::text[]
    )
    ON CONFLICT DO NOTHING
"""
# Forward only, and at most once a minute per key: the column says roughly
# when, and a busy key must not be rewritten on every batch.
_LAST_USED = """
    UPDATE api_keys AS k SET last_used_at = u.ts
      FROM unnest($1::uuid[], $2::timestamptz[]) AS u(id, ts)
     WHERE k.id = u.id
       AND (k.last_used_at IS NULL OR k.last_used_at < u.ts - interval '1 minute')
"""
_ENSURE_PARTITIONS = "SELECT ensure_request_log_partitions($1::date[])"
_DROP_PARTITIONS = "SELECT drop_request_log_partitions($1)"

# What a stream read returns: (entry id, fields) pairs.
Entry = tuple[str, dict[str, str]]


@dataclass
class Totals:
    """What the worker did since it started (logged once a minute)."""

    stored: int = 0  # rows inserted
    repeated: int = 0  # entries already stored (read again after a failure)
    dropped: int = 0  # malformed or out-of-window entries


@dataclass
class Ingester:
    redis: Redis
    pool: asyncpg.Pool
    consumer: str  # this worker's name in the group, e.g. its host name
    retention_days: int = DEFAULT_RETENTION_DAYS
    stream: str = STREAM
    group: str = GROUP
    batch: int = BATCH
    claim_idle_ms: int = CLAIM_IDLE_MS
    block_ms: int = BLOCK_MS
    totals: Totals = field(default_factory=Totals)
    # Our own entries not acknowledged yet (from before a failure or a
    # restart) are read again before new ones.
    _pending_first: bool = True
    _months: set[date] = field(default_factory=set)  # partitions known to exist

    async def ensure_group(self) -> None:
        """Create the consumer group (and the stream) if missing.

        From "0": entries the gateway wrote before any worker ran are read too.
        """
        try:
            await self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def ingest_once(self) -> int:
        """Read and store one batch; returns how many entries it handled.

        Our own backlog first (entries read before a failure or a restart
        and never acknowledged), then new entries, waiting up to block_ms.
        """
        entries: list[Entry] = []
        if self._pending_first:
            entries = await self._read("0", block=None)
            self._pending_first = bool(entries)
        if not entries:
            entries = await self._read(">", block=self.block_ms)
        await self.store(entries)
        return len(entries)

    async def _read(self, start: str, *, block: int | None) -> list[Entry]:
        answer = await self.redis.xreadgroup(
            self.group, self.consumer, {self.stream: start}, count=self.batch, block=block
        )
        return answer[0][1] if answer else []

    async def claim_stale(self) -> int:
        """Take over entries a stopped worker left pending; returns how many."""
        handled = 0
        cursor = "0-0"
        while True:
            # Entries trimmed from the stream while pending come back as
            # `deleted`; Redis has already taken them off the pending list.
            cursor, entries, deleted = await self.redis.xautoclaim(
                self.stream,
                self.group,
                self.consumer,
                min_idle_time=self.claim_idle_ms,
                start_id=cursor,
                count=self.batch,
            )
            await self.store(entries)
            handled += len(entries) + len(deleted)
            if cursor == "0-0":
                return handled

    async def store(self, entries: Sequence[Entry]) -> None:
        """Store a batch, then acknowledge and delete it from the stream."""
        if not entries:
            return
        events = self._valid(entries)
        if events:
            months = {date(e.ts.year, e.ts.month, 1) for e in events} - self._months
            last_used: dict[uuid.UUID, datetime] = {}
            for e in events:
                if e.key_id is not None:
                    last_used[e.key_id] = max(e.ts, last_used.get(e.key_id, e.ts))
            async with self.pool.acquire() as conn, conn.transaction():
                if months:
                    await conn.execute(_ENSURE_PARTITIONS, sorted(months))
                status = await conn.execute(_INSERT, *_columns(events))
                if last_used:
                    # Sorted, so two workers lock the same keys in the same order.
                    keys = sorted(last_used)
                    await conn.execute(_LAST_USED, keys, [last_used[k] for k in keys])
            self._months |= months
            stored = int(status.split()[-1])  # "INSERT 0 412"
            self.totals.stored += stored
            self.totals.repeated += len(events) - stored
        ids = [entry_id for entry_id, _ in entries]
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.xack(self.stream, self.group, *ids)
            pipe.xdel(self.stream, *ids)
            await pipe.execute()

    def _valid(self, entries: Sequence[Entry]) -> list[UsageEvent]:
        """The entries that are well-formed and in the stored window."""
        now = datetime.now(UTC)
        oldest = now - timedelta(days=self.retention_days)
        events: list[UsageEvent] = []
        for entry_id, fields in entries:
            try:
                event = UsageEvent.model_validate(fields)
            except ValidationError as e:
                self._drop(entry_id, f"malformed ({e.error_count()} errors)")
                continue
            if not oldest <= event.ts <= now + MAX_CLOCK_SKEW:
                self._drop(entry_id, "outside the stored window")
                continue
            events.append(event)
        return events

    def _drop(self, entry_id: str, reason: str) -> None:
        self.totals.dropped += 1
        logger.warning("usage event %s dropped: %s", entry_id, reason)

    async def drop_expired(self) -> int:
        """Drop request log months older than the retention period."""
        async with self.pool.acquire() as conn:
            dropped: int = await conn.fetchval(_DROP_PARTITIONS, self.retention_days)
        self._months.clear()  # some may be gone now
        return dropped

    async def run(self, stop: asyncio.Event) -> None:
        """Ingest until `stop` is set; retry with backoff while Redis or Postgres is down."""
        backoff = 1.0
        next_claim = next_retention = next_report = 0.0
        group_ready = False
        while not stop.is_set():
            try:
                if not group_ready:
                    await self.ensure_group()
                    group_ready = True
                now = time.monotonic()
                if now >= next_retention:
                    if dropped := await self.drop_expired():
                        logger.info("dropped %d request log months", dropped)
                    next_retention = now + DAY_SECONDS
                if now >= next_claim:
                    if claimed := await self.claim_stale():
                        logger.info("claimed %d usage events of a stopped worker", claimed)
                    next_claim = now + CLAIM_EVERY
                await self.ingest_once()
                if now >= next_report:
                    self._report()
                    next_report = now + 60
                backoff = 1.0
            except (RedisError, OSError, asyncpg.PostgresError, asyncpg.InterfaceError):
                logger.exception("usage ingest failed; trying again in %.0f s", backoff)
                self._pending_first = True  # what was read stays pending: read it again
                group_ready = False  # Redis may have restarted empty
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

    def _report(self) -> None:
        t = self.totals
        if t.stored or t.dropped:
            logger.info(
                "usage ingest totals: %d stored, %d repeated, %d dropped",
                t.stored,
                t.repeated,
                t.dropped,
            )


def _columns(events: Sequence[UsageEvent]) -> list[list[object]]:
    """The events as one list per column, for the unnest() insert."""
    return [
        [e.id for e in events],
        [e.ts for e in events],
        [e.workspace_id for e in events],
        [e.request_id for e in events],
        [e.method for e in events],
        [e.path for e in events],
        [e.route_id for e in events],
        [e.target_id for e in events],
        [e.consumer_id for e in events],
        [e.key_id for e in events],
        [e.status for e in events],
        [e.latency_ms for e in events],
        [e.upstream_ms for e in events],
        [e.bytes for e in events],
        [str(e.client_ip) if e.client_ip else None for e in events],
        [e.ratelimit for e in events],
    ]


async def open_pool(database_url: str) -> asyncpg.Pool:
    """A small pool: one batch at a time, plus retention.

    min_size=0 connects on first use, so a database that is down at start
    is retried by run() like any later failure.
    """
    pool: asyncpg.Pool = await asyncpg.create_pool(
        asyncpg_dsn(database_url), min_size=0, max_size=2
    )
    return pool
