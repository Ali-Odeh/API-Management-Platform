"""The usage-ingest worker (app/usage/ingest.py) against real Postgres and Redis (V2.1).

Every test uses its own stream key in Redis database 15 (REDIS_TEST_URL),
so it never sees another test's entries; without REDIS_TEST_URL the tests
are skipped (check.ps1's container and CI set it).
"""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import driver_dsn
from app.gateway_config.crud import get_config_seq
from app.keys.models import ApiKey
from app.usage.events import UsageEvent
from app.usage.ingest import Ingester, open_pool
from app.workspaces.models import Workspace
from tests.factories import make_api_key, make_consumer

INGEST_URL = os.environ["INGEST_DATABASE_URL"]
APP_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    url = os.environ.get("REDIS_TEST_URL")
    if not url:
        pytest.skip("REDIS_TEST_URL not set (Docker's Redis, database 15)")
    client = Redis.from_url(url, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def stream(redis: Redis) -> AsyncIterator[str]:
    key = f"usage:test:{uuid.uuid4()}"
    yield key
    await redis.delete(key)


@pytest.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    p = await open_pool(INGEST_URL)
    yield p
    await p.close()


@pytest.fixture
async def ingester(redis: Redis, pool: asyncpg.Pool, stream: str) -> Ingester:
    worker = Ingester(redis, pool, consumer="worker-a", stream=stream, block_ms=10)
    await worker.ensure_group()
    return worker


def event(**fields: Any) -> dict[str, str]:
    """A valid stream entry, as the gateway writes it; fields override."""
    now = datetime.now(UTC)
    base = {
        "v": "1",
        "id": str(uuid.uuid4()),
        "ts": str(int(now.timestamp() * 1_000_000)),
        "workspace_id": str(uuid.uuid4()),
        "request_id": "req-1",
        "method": "GET",
        "path": "/v1/orders/7",
        "status": "200",
        "latency_ms": "1.25",
        "upstream_ms": "0.9",
        "bytes": "42",
        "client_ip": "203.0.113.7",
        "ratelimit": "ok",
    }
    base.update({k: str(v) for k, v in fields.items()})
    return {k: v for k, v in base.items() if v != ""}


def micros(ts: datetime) -> str:
    return str(int(ts.timestamp() * 1_000_000))


async def ingest_all(worker: Ingester) -> None:
    """Read until the stream has nothing new for this worker."""
    while await worker.ingest_once():
        pass


async def rows(db: AsyncSession, workspace_id: str) -> list[dict[str, Any]]:
    result = await db.execute(
        text("SELECT * FROM request_logs WHERE workspace_id = :ws ORDER BY ts, event_id"),
        {"ws": workspace_id},
    )
    return [dict(r._mapping) for r in result]


# ---- Each event stored once


async def test_every_event_becomes_one_row_and_leaves_the_stream(
    ingester: Ingester, redis: Redis, stream: str, db: AsyncSession
) -> None:
    acme, globex = str(uuid.uuid4()), str(uuid.uuid4())
    route, consumer, key = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sent = [
        event(workspace_id=acme, route_id=route, consumer_id=consumer, key_id=key),
        event(workspace_id=acme, status=429, ratelimit="limited", upstream_ms=0),
        event(workspace_id=globex, method="POST", client_ip="2001:db8::1"),
    ]
    for fields in sent:
        await redis.xadd(stream, fields)

    await ingest_all(ingester)

    stored = await rows(db, acme)
    assert len(stored) == 2
    first = next(r for r in stored if str(r["event_id"]) == sent[0]["id"])
    assert (first["route_id"], first["consumer_id"], first["key_id"]) == (route, consumer, key)
    assert (first["method"], first["path"], first["status"], first["bytes"]) == (
        "GET",
        "/v1/orders/7",
        200,
        42,
    )
    assert (first["latency_ms"], first["upstream_ms"], str(first["client_ip"])) == (
        1.25,
        0.9,
        "203.0.113.7",
    )
    assert first["ts"] == datetime.fromtimestamp(int(sent[0]["ts"]) / 1e6, tz=UTC)
    [other] = await rows(db, globex)
    assert str(other["client_ip"]) == "2001:db8::1"
    # Stored, so acknowledged and gone from the stream.
    assert await redis.xlen(stream) == 0
    assert (await redis.xpending(stream, "usage-ingest"))["pending"] == 0
    assert (ingester.totals.stored, ingester.totals.dropped) == (3, 0)


async def test_an_event_read_again_is_stored_once(
    ingester: Ingester, redis: Redis, stream: str, db: AsyncSession
) -> None:
    fields = event()
    await redis.xadd(stream, fields)
    await redis.xadd(stream, fields)  # e.g. read again after a failure to acknowledge

    await ingest_all(ingester)

    assert len(await rows(db, fields["workspace_id"])) == 1
    assert (ingester.totals.stored, ingester.totals.repeated) == (1, 1)


@pytest.mark.parametrize(
    "bad",
    [
        {"v": "2"},
        {"id": "not-a-uuid"},
        {"workspace_id": ""},
        {"status": "999"},
        {"latency_ms": "-1"},
        {"client_ip": "999.1.1.1"},
        {"method": ""},
        {"ts": micros(datetime.now(UTC) - timedelta(days=91))},  # past the retention
        {"ts": micros(datetime.now(UTC) + timedelta(days=2))},  # a broken clock
    ],
)
async def test_a_bad_entry_is_dropped_and_acknowledged(
    ingester: Ingester, redis: Redis, stream: str, db: AsyncSession, bad: dict[str, str]
) -> None:
    good = event()
    broken = {**event(workspace_id=good["workspace_id"]), **bad}
    await redis.xadd(stream, {k: v for k, v in broken.items() if v != ""})
    await redis.xadd(stream, good)

    await ingest_all(ingester)

    stored = await rows(db, good["workspace_id"])
    assert [str(r["event_id"]) for r in stored] == [good["id"]]  # the batch went on
    assert ingester.totals.dropped == 1
    assert await redis.xlen(stream) == 0


# ---- Nothing lost when a worker stops


async def test_a_worker_stopped_mid_batch_loses_and_doubles_nothing(
    ingester: Ingester, redis: Redis, stream: str, db: AsyncSession
) -> None:
    workspace = str(uuid.uuid4())
    for _ in range(5):
        await redis.xadd(stream, event(workspace_id=workspace))
    # Worker A reads the batch and dies before its commit (its pool is gone).
    await ingester.pool.close()
    with pytest.raises(asyncpg.InterfaceError):
        await ingester.ingest_once()
    assert (await redis.xpending(stream, "usage-ingest"))["pending"] == 5
    assert await rows(db, workspace) == []

    # Worker B takes over what A left pending (idle long enough: here at once).
    pool_b = await open_pool(INGEST_URL)
    try:
        worker_b = Ingester(
            redis, pool_b, consumer="worker-b", stream=stream, claim_idle_ms=0, block_ms=10
        )
        assert await worker_b.claim_stale() == 5
        # A comes back and reads its own backlog first: B has it, nothing doubles.
        worker_a = Ingester(redis, pool_b, consumer="worker-a", stream=stream, block_ms=10)
        await ingest_all(worker_a)
    finally:
        await pool_b.close()

    assert len(await rows(db, workspace)) == 5
    assert (await redis.xpending(stream, "usage-ingest"))["pending"] == 0
    assert await redis.xlen(stream) == 0


async def test_a_restarted_worker_reads_its_own_backlog_first(
    ingester: Ingester, redis: Redis, stream: str, pool: asyncpg.Pool, db: AsyncSession
) -> None:
    workspace = str(uuid.uuid4())
    await redis.xadd(stream, event(workspace_id=workspace))
    # Read but never stored: pending under worker-a.
    await redis.xreadgroup("usage-ingest", "worker-a", {stream: ">"}, count=10)

    restarted = Ingester(redis, pool, consumer="worker-a", stream=stream, block_ms=10)
    await ingest_all(restarted)

    assert len(await rows(db, workspace)) == 1


async def test_entries_trimmed_while_pending_are_simply_gone(
    ingester: Ingester, redis: Redis, stream: str
) -> None:
    entry_id = await redis.xadd(stream, event())
    await redis.xreadgroup("usage-ingest", "worker-gone", {stream: ">"}, count=10)
    await redis.xdel(stream, entry_id)  # trimmed by MAXLEN while nobody stored it

    ingester.claim_idle_ms = 0
    assert await ingester.claim_stale() == 1
    assert (await redis.xpending(stream, "usage-ingest"))["pending"] == 0


# ---- last_used_at


async def test_last_used_at_moves_forward_at_most_once_a_minute(
    ingester: Ingester, redis: Redis, stream: str, db: AsyncSession, workspace: Workspace
) -> None:
    consumer = await make_consumer(db, workspace.id)
    key = await make_api_key(db, workspace.id, consumer.id)
    key_id, updated_before = key.id, key.updated_at
    seq_before = (await get_config_seq(db)).seq
    t0 = datetime.now(UTC) - timedelta(minutes=10)

    async def use(at: datetime) -> datetime | None:
        await redis.xadd(stream, event(key_id=key_id, ts=micros(at)))
        await ingest_all(ingester)
        db.expire_all()
        return await db.scalar(select(ApiKey.last_used_at).where(ApiKey.id == key_id))

    assert await use(t0) == t0.replace(microsecond=t0.microsecond)
    assert await use(t0 + timedelta(seconds=30)) == t0  # within a minute: not rewritten
    assert await use(t0 - timedelta(minutes=5)) == t0  # older: never backwards
    later = t0 + timedelta(minutes=2)
    assert await use(later) == later

    # Being used isn't an edit of the key, nor a config change.
    db.expire_all()
    assert await db.scalar(select(ApiKey.updated_at).where(ApiKey.id == key_id)) == updated_before
    assert (await get_config_seq(db)).seq == seq_before


async def test_a_batch_with_one_key_twice_takes_its_latest_use(
    ingester: Ingester, redis: Redis, stream: str, db: AsyncSession, workspace: Workspace
) -> None:
    consumer = await make_consumer(db, workspace.id)
    key = await make_api_key(db, workspace.id, consumer.id)
    early, late = datetime.now(UTC) - timedelta(minutes=3), datetime.now(UTC) - timedelta(minutes=1)
    await redis.xadd(stream, event(key_id=key.id, ts=micros(late)))
    await redis.xadd(stream, event(key_id=key.id, ts=micros(early)))

    await ingest_all(ingester)

    assert await db.scalar(select(ApiKey.last_used_at).where(ApiKey.id == key.id)) == late.replace(
        microsecond=late.microsecond
    )


# ---- Partitions and retention


async def test_retention_drops_only_months_past_the_period(
    ingester: Ingester, pool: asyncpg.Pool
) -> None:
    today = datetime.now(UTC).date()
    long_ago = (today.replace(day=1) - timedelta(days=200)).replace(day=1)
    async with pool.acquire() as conn:
        await conn.execute("SELECT ensure_request_log_partitions($1::date[])", [long_ago, today])
        names = await conn.fetch(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE i.inhparent = 'request_logs'::regclass"
        )
    old_name = f"request_logs_y{long_ago:%Y}m{long_ago:%m}"
    this_month = f"request_logs_y{today:%Y}m{today:%m}"
    assert {old_name, this_month} <= {r["relname"] for r in names}

    ingester.retention_days = 90
    assert await ingester.drop_expired() >= 1

    async with pool.acquire() as conn:
        left = {
            r["relname"]
            for r in await conn.fetch(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = 'request_logs'::regclass"
            )
        }
        with pytest.raises(asyncpg.RaiseError, match="at least 1"):
            await conn.execute("SELECT drop_request_log_partitions(0)")
    assert old_name not in left and this_month in left


async def test_a_new_month_is_made_on_first_use(
    ingester: Ingester, redis: Redis, stream: str, db: AsyncSession, pool: asyncpg.Pool
) -> None:
    # Drop every partition the test database has, then store an event.
    ingester.retention_days = 1
    async with pool.acquire() as conn:
        await conn.execute("SELECT drop_request_log_partitions(1)")
    ingester._months.clear()
    fields = event()
    await redis.xadd(stream, fields)

    await ingest_all(ingester)

    assert len(await rows(db, fields["workspace_id"])) == 1


# ---- Least privilege


async def _connect(url: str) -> asyncpg.Connection:
    return await asyncpg.connect(driver_dsn(url))


async def test_the_ingest_login_may_only_insert_logs_and_set_last_used() -> None:
    conn = await _connect(INGEST_URL)
    try:
        for statement in (
            "SELECT count(*) FROM workspaces",
            "SELECT count(*) FROM request_logs",
            "UPDATE api_keys SET name = name",
            "DELETE FROM request_logs",
            "SELECT bump_workspaces(ARRAY[]::uuid[])",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(statement)
    finally:
        await conn.close()


async def test_the_api_may_only_read_the_request_log() -> None:
    conn = await _connect(APP_URL)
    try:
        await conn.fetchval("SELECT count(*) FROM request_logs")
        part = await conn.fetchval(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE i.inhparent = 'request_logs'::regclass LIMIT 1"
        )
        statements = [
            "INSERT INTO request_logs (event_id, ts, workspace_id, method, path, status, "
            "latency_ms, upstream_ms, bytes) VALUES (gen_random_uuid(), now(), gen_random_uuid(), "
            "'GET', '/', 200, 1, 0, 0)",
            "DELETE FROM request_logs",
            "SELECT ensure_request_log_partitions(ARRAY[current_date])",
        ]
        if part:  # a partition's own privileges are none: everything goes through the parent
            statements.append(f"SELECT count(*) FROM {part}")
        for statement in statements:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(statement)
    finally:
        await conn.close()


# ---- The loop


async def test_run_ingests_until_stopped(
    ingester: Ingester, redis: Redis, stream: str, db: AsyncSession
) -> None:
    fields = event()
    stop = asyncio.Event()
    task = asyncio.create_task(ingester.run(stop))
    await redis.xadd(stream, fields)
    for _ in range(50):
        if await rows(db, fields["workspace_id"]):
            break
        await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert len(await rows(db, fields["workspace_id"])) == 1


async def test_run_keeps_trying_while_redis_is_down(
    pool: asyncpg.Pool, caplog: pytest.LogCaptureFixture
) -> None:
    down = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, decode_responses=True)
    worker = Ingester(down, pool, consumer="lonely")
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, timeout=5)  # the backoff wait ends when stopped
    await down.aclose()
    assert "usage ingest failed" in caplog.text


async def test_ensure_group_is_safe_to_repeat_but_reports_other_errors(
    ingester: Ingester, redis: Redis, stream: str
) -> None:
    await ingester.ensure_group()  # BUSYGROUP: already there
    await redis.delete(stream)
    await redis.set(stream, "not a stream")
    with pytest.raises(ResponseError, match="WRONGTYPE"):
        await ingester.ensure_group()


def test_the_stream_entry_reads_empty_fields_as_none() -> None:
    parsed = UsageEvent.model_validate(event(route_id="", client_ip=""))
    assert (parsed.route_id, parsed.client_ip, parsed.consumer_id) == (None, None, None)


def test_the_worker_needs_none_of_the_apis_settings() -> None:
    # Its container gets only INGEST_DATABASE_URL, REDIS_URL and the period.
    env = {
        "PATH": os.environ["PATH"],
        "INGEST_DATABASE_URL": INGEST_URL,
        "REDIS_URL": "redis://redis:6379/0",
    }
    done = subprocess.run(
        [sys.executable, "-c", "import app.usage.ingest, scripts.usage_ingest"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
