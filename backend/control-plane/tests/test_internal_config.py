"""The gateway's config: snapshot, internal token, ETag/304 and long polling."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import driver_dsn, engine
from app.core.deps import InternalOnly
from app.gateway_config import crud as gateway_crud
from app.gateway_config.crud import read_snapshot, read_version
from app.gateway_config.notifier import ConfigNotifier
from app.gateway_config.provider import ConfigProvider
from app.gateway_config.schemas import ConfigSnapshot
from app.main import app
from app.workspaces.models import Workspace
from tests.factories import make_api_key, make_consumer, make_route, make_service

# =====================================================================
# The snapshot
# =====================================================================


async def test_empty_snapshot() -> None:
    snapshot = await read_snapshot(engine)
    assert snapshot.version == 1
    assert snapshot.routes == []
    assert snapshot.keys == []


async def test_snapshot_joins_routes_with_their_service(
    db: AsyncSession, workspace: Workspace
) -> None:
    orders = await make_service(
        db, workspace.id, name="orders", upstream_url="http://orders:9000", timeout_ms=3000
    )
    route = await make_route(
        db,
        workspace.id,
        orders.id,
        path_prefix="/v1/orders",
        methods=["GET", "POST"],
        strip_prefix=True,
        auth_required=False,
    )

    snapshot = await read_snapshot(engine)
    assert [r.model_dump() for r in snapshot.routes] == [
        {
            "id": route.id,
            "path_prefix": "/v1/orders",
            "methods": ["GET", "POST"],
            "strip_prefix": True,
            "auth_required": False,
            "upstream_url": "http://orders:9000",
            "timeout_ms": 3000,
        }
    ]


async def test_snapshot_leaves_out_what_is_switched_off(
    db: AsyncSession, workspace: Workspace
) -> None:
    live = await make_service(db, workspace.id, name="live")
    off = await make_service(db, workspace.id, name="off", is_active=False)
    await make_route(db, workspace.id, live.id, path_prefix="/b-live")
    await make_route(db, workspace.id, live.id, path_prefix="/a-route-off", is_active=False)
    await make_route(db, workspace.id, off.id, path_prefix="/c-service-off")

    snapshot = await read_snapshot(engine)
    assert [r.path_prefix for r in snapshot.routes] == ["/b-live"]


async def test_snapshot_routes_are_sorted_by_prefix(db: AsyncSession, workspace: Workspace) -> None:
    service = await make_service(db, workspace.id)
    for prefix in ["/v2/orders", "/", "/v1/orders"]:
        await make_route(db, workspace.id, service.id, path_prefix=prefix)
    snapshot = await read_snapshot(engine)
    assert [r.path_prefix for r in snapshot.routes] == ["/", "/v1/orders", "/v2/orders"]


async def test_snapshot_keys(db: AsyncSession, workspace: Workspace) -> None:
    active = await make_consumer(db, workspace.id, name="active-app", rate_limit_per_min=120)
    blocked = await make_consumer(db, workspace.id, name="blocked-app", status="blocked")
    good = await make_api_key(db, workspace.id, active.id)
    revoked = await make_api_key(db, workspace.id, active.id)
    revoked.revoked_at = good.created_at
    await db.commit()
    blocked_key = await make_api_key(db, workspace.id, blocked.id)

    snapshot = await read_snapshot(engine)
    by_id = {k.id: k for k in snapshot.keys}

    # Revoked keys are left out entirely: the gateway answers 401 for them.
    assert set(by_id) == {good.id, blocked_key.id}
    assert by_id[good.id].key_hash == good.key_hash
    assert by_id[good.id].consumer_status == "active"
    assert by_id[good.id].rate_limit_per_min == 120
    # Blocked consumers' keys stay in, marked, so the gateway can answer 403.
    assert by_id[blocked_key.id].consumer_status == "blocked"
    # Sorted by id, so the same version always gives the same JSON.
    assert [k.id for k in snapshot.keys] == sorted(by_id)


async def test_snapshot_version_follows_changes(db: AsyncSession, workspace: Workspace) -> None:
    await make_consumer(db, workspace.id)
    assert (await read_snapshot(engine)).version == await read_version(engine) == 2


async def test_snapshot_covers_every_workspace(
    db: AsyncSession, workspace: Workspace, other_workspace: Workspace
) -> None:
    # MVP: one global snapshot. The gateway serves all tenants.
    ours = await make_service(db, workspace.id, name="ours")
    theirs = await make_service(db, other_workspace.id, name="theirs")
    await make_route(db, workspace.id, ours.id, path_prefix="/ours")
    await make_route(db, other_workspace.id, theirs.id, path_prefix="/theirs")
    snapshot = await read_snapshot(engine)
    assert {r.path_prefix for r in snapshot.routes} == {"/ours", "/theirs"}


# =====================================================================
# The internal token check
# =====================================================================


@pytest.fixture
async def internal_client() -> AsyncClient:
    mini = FastAPI()

    @mini.get("/internal/ping", dependencies=[InternalOnly])
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    return AsyncClient(transport=ASGITransport(app=mini), base_url="http://t")


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"X-Internal-Token": "wrong"}, 401),
        ({"X-Internal-Token": ""}, 401),
        ({"X-Internal-Token": get_settings().internal_token}, 200),
    ],
)
async def test_internal_token(
    internal_client: AsyncClient, headers: dict[str, str], status: int
) -> None:
    r = await internal_client.get("/internal/ping", headers=headers)
    assert r.status_code == status
    if status == 401:
        assert r.json() == {"detail": "Invalid internal token"}


async def test_admin_jwt_does_not_open_internal_endpoints(
    internal_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    assert (await internal_client.get("/internal/ping", headers=auth_headers)).status_code == 401


# =====================================================================
# GET /internal/config: ETag, 304 and long polling
# =====================================================================

URL = "/internal/config"


@pytest.fixture
async def notifier() -> AsyncIterator[ConfigNotifier]:
    listening = ConfigNotifier(driver_dsn(get_settings().database_url), reconnect_delay=0.1)
    await listening.start()
    await listening.wait_until_listening()
    yield listening
    await listening.stop()


@pytest.fixture
def gateway(client: AsyncClient, notifier: ConfigNotifier) -> AsyncClient:
    """The test client, set up like the real gateway: provider running, token sent."""
    app.state.config_provider = ConfigProvider(engine, notifier)
    client.headers["X-Internal-Token"] = get_settings().internal_token
    return client


async def change_config(client: AsyncClient, auth_headers: dict[str, str], name: str) -> None:
    """An admin change through the real API (bumps the version, sends NOTIFY)."""
    body = {"name": name, "email": "dev@example.com"}
    assert (
        await client.post("/api/v1/consumers", json=body, headers=auth_headers)
    ).status_code == 201


async def test_needs_the_internal_token(client: AsyncClient, notifier: ConfigNotifier) -> None:
    app.state.config_provider = ConfigProvider(engine, notifier)
    assert (await client.get(URL)).status_code == 401


async def test_first_call_returns_snapshot_with_etag(gateway: AsyncClient) -> None:
    r = await gateway.get(URL)
    assert r.status_code == 200
    assert r.headers["ETag"] == '"1"'
    assert r.headers["Cache-Control"] == "no-cache"
    assert r.json() == {"version": 1, "routes": [], "keys": []}


@pytest.mark.parametrize("etag", ['"1"', 'W/"1"', "1"])
async def test_same_version_is_304(gateway: AsyncClient, etag: str) -> None:
    r = await gateway.get(URL, headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["ETag"] == '"1"'


@pytest.mark.parametrize("etag", ['"0"', "garbage", ""])
async def test_other_or_unreadable_version_gets_the_snapshot(
    gateway: AsyncClient, etag: str
) -> None:
    r = await gateway.get(URL, headers={"If-None-Match": etag})
    assert r.status_code == 200
    assert r.json()["version"] == 1


@pytest.mark.parametrize("wait", [-1, 61, "soon"])
async def test_wait_out_of_range_is_422(gateway: AsyncClient, wait: object) -> None:
    assert (await gateway.get(URL, params={"wait": wait})).status_code == 422


async def test_long_poll_without_change_ends_with_304(gateway: AsyncClient) -> None:
    started = time.monotonic()
    r = await gateway.get(URL, params={"wait": 1}, headers={"If-None-Match": '"1"'})
    assert r.status_code == 304
    assert time.monotonic() - started >= 0.9  # it really held the request


async def test_long_poll_answers_the_moment_config_changes(
    gateway: AsyncClient, auth_headers: dict[str, str]
) -> None:
    waiting = asyncio.create_task(
        gateway.get(URL, params={"wait": 30}, headers={"If-None-Match": '"1"'})
    )
    await asyncio.sleep(0.3)
    assert not waiting.done()  # held open: nothing has changed yet

    changed_at = time.monotonic()
    await change_config(gateway, auth_headers, "mobile-app")
    r = await asyncio.wait_for(waiting, timeout=5)

    assert time.monotonic() - changed_at < 1  # not 30 seconds: woken by NOTIFY
    assert r.status_code == 200
    assert r.headers["ETag"] == '"2"'
    assert r.json()["version"] == 2


async def test_missed_notify_is_still_answered_correctly(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # A notifier that never started: no NOTIFY will ever wake the request.
    deaf = ConfigNotifier(driver_dsn(get_settings().database_url))
    app.state.config_provider = ConfigProvider(engine, deaf)
    client.headers["X-Internal-Token"] = get_settings().internal_token

    waiting = asyncio.create_task(
        client.get(URL, params={"wait": 1}, headers={"If-None-Match": '"1"'})
    )
    await asyncio.sleep(0.2)
    await change_config(client, auth_headers, "mobile-app")
    r = await waiting
    # It slept the full second, but re-checked the database before answering.
    assert r.status_code == 200
    assert r.json()["version"] == 2


async def test_listener_reconnects_after_losing_its_connection(
    gateway: AsyncClient,
    notifier: ConfigNotifier,
    db: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    old_pid = notifier.backend_pid
    await db.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": old_pid})
    await db.commit()

    for _ in range(50):  # wait for the reconnect (backoff is 0.1 s in tests)
        if notifier.backend_pid != old_pid:
            break
        await asyncio.sleep(0.1)
    await notifier.wait_until_listening()
    assert notifier.backend_pid != old_pid

    waiting = asyncio.create_task(
        gateway.get(URL, params={"wait": 30}, headers={"If-None-Match": '"1"'})
    )
    await asyncio.sleep(0.2)
    await change_config(gateway, auth_headers, "mobile-app")
    assert (await asyncio.wait_for(waiting, timeout=5)).status_code == 200


async def test_many_waiting_gateways_share_one_snapshot_build(
    gateway: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = 0
    real_read_snapshot = gateway_crud.read_snapshot

    async def counting_read_snapshot(*args: Any, **kwargs: Any) -> ConfigSnapshot:
        nonlocal builds
        builds += 1
        return await real_read_snapshot(*args, **kwargs)

    monkeypatch.setattr(gateway_crud, "read_snapshot", counting_read_snapshot)

    waiting = [
        asyncio.create_task(gateway.get(URL, params={"wait": 30}, headers={"If-None-Match": '"1"'}))
        for _ in range(5)
    ]
    await asyncio.sleep(0.3)
    await change_config(gateway, auth_headers, "mobile-app")
    results = await asyncio.wait_for(asyncio.gather(*waiting), timeout=5)

    assert {r.status_code for r in results} == {200}
    assert {r.json()["version"] for r in results} == {2}
    assert builds == 1  # five gateways woke up, Postgres built the snapshot once


async def test_stopping_the_notifier_releases_waiting_requests(notifier: ConfigNotifier) -> None:
    provider = ConfigProvider(engine, notifier)
    waiting = asyncio.create_task(provider.get_if_changed(known_version=1, wait=30))
    await asyncio.sleep(0.2)
    started = time.monotonic()
    await notifier.stop()
    assert await asyncio.wait_for(waiting, timeout=5) is None  # nothing changed -> 304
    assert time.monotonic() - started < 1


async def test_later_gateways_reuse_the_built_snapshot(
    gateway: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = 0
    real_read_snapshot = gateway_crud.read_snapshot

    async def counting_read_snapshot(*args: Any, **kwargs: Any) -> ConfigSnapshot:
        nonlocal builds
        builds += 1
        return await real_read_snapshot(*args, **kwargs)

    monkeypatch.setattr(gateway_crud, "read_snapshot", counting_read_snapshot)
    for _ in range(3):  # three gateways starting up one after another
        assert (await gateway.get(URL)).status_code == 200
    assert builds == 1


async def test_listener_keeps_retrying_while_postgres_is_unreachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Port 1: nothing listens there, so every connection attempt fails.
    unreachable = ConfigNotifier("postgresql://app:app@127.0.0.1:1/apim", reconnect_delay=0.05)
    with caplog.at_level(logging.WARNING, logger="app.gateway_config.notifier"):
        await unreachable.start()
        await asyncio.sleep(0.5)
        with pytest.raises(TimeoutError):
            await unreachable.wait_until_listening(seconds=0.1)
        await unreachable.stop()  # still stops cleanly

    retries = [r for r in caplog.records if "can't listen, retrying" in r.getMessage()]
    assert len(retries) >= 2  # it kept trying instead of giving up or crashing


async def test_stop_without_start_is_harmless() -> None:
    never_started = ConfigNotifier(driver_dsn(get_settings().database_url))
    waiting = asyncio.create_task(never_started.wait(never_started.subscribe(), seconds=30))
    await asyncio.sleep(0.05)
    await never_started.stop()
    await asyncio.wait_for(waiting, timeout=1)  # the waiter was released
