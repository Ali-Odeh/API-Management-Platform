"""The gateway's config (MT.6): full and incremental answers, the internal
token, 304 and long polling."""

import asyncio
import logging
import time
import uuid
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
from app.gateway_config.crud import read_answer, read_seq
from app.gateway_config.notifier import ConfigNotifier
from app.gateway_config.provider import ConfigProvider
from app.gateway_config.schemas import ConfigAnswer, WorkspaceConfigOut
from app.main import app
from app.workspaces.models import Workspace, WorkspaceStatus
from tests.factories import make_api_key, make_consumer, make_route, make_service, make_workspace
from tests.test_api_guards import set_status


async def _answer(since: int = 0, slugs: frozenset[str] | None = None) -> ConfigAnswer:
    return await read_answer(engine, since=since, slugs=slugs, cache={})


def _by_slug(answer: ConfigAnswer) -> dict[str, WorkspaceConfigOut]:
    return {w.slug: w for w in answer.workspaces}


# =====================================================================
# What a workspace's config holds
# =====================================================================


async def test_a_full_answer_has_every_served_workspace(workspace: Workspace) -> None:
    answer = await _answer()
    assert (answer.seq, answer.full, answer.deleted) == (2, True, [])  # default's insert: 2
    [default] = answer.workspaces
    assert default.model_dump() == {
        "id": workspace.id,
        "slug": "default",
        "status": WorkspaceStatus.ACTIVE,
        "version": 1,
        "hosts": ["default"],
        "limits": {"rate_limit_per_min": 6000, "max_concurrent": 200},
        "services": [],
        "routes": [],
        "keys": [],
    }


async def test_services_routes_and_keys(db: AsyncSession, workspace: Workspace) -> None:
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
    consumer = await make_consumer(db, workspace.id, rate_limit_per_min=120)
    key = await make_api_key(db, workspace.id, consumer.id)

    [default] = (await _answer()).workspaces

    assert [s.model_dump() for s in default.services] == [
        {
            "id": orders.id,
            "timeout_ms": 3000,
            # Until MT.10 a service's upstream_url is its one target.
            "targets": [{"id": orders.id, "url": "http://orders:9000", "weight": 100}],
        }
    ]
    assert [r.model_dump() for r in default.routes] == [
        {
            "id": route.id,
            "service_id": orders.id,
            "path_prefix": "/v1/orders",
            "methods": ["GET", "POST"],
            "strip_prefix": True,
            "auth_required": False,
        }
    ]
    assert [k.model_dump() for k in default.keys] == [
        {
            "id": key.id,
            "key_hash": key.key_hash,
            "consumer_id": consumer.id,
            "consumer_status": "active",
            "rate_limit_per_min": 120,
        }
    ]


async def test_what_is_switched_off_is_left_out(db: AsyncSession, workspace: Workspace) -> None:
    off = await make_service(db, workspace.id, name="off", is_active=False)
    await make_route(db, workspace.id, off.id, path_prefix="/v1/off")  # its service is off
    on = await make_service(db, workspace.id, name="on")
    await make_route(db, workspace.id, on.id, path_prefix="/v1/disabled", is_active=False)
    consumer = await make_consumer(db, workspace.id, status="blocked")
    blocked = await make_api_key(db, workspace.id, consumer.id)
    await make_api_key(db, workspace.id, consumer.id, revoked_at=blocked.created_at)

    [default] = (await _answer()).workspaces

    assert [s.id for s in default.services] == [on.id]
    assert default.routes == []
    # Blocked consumers' keys stay (the gateway answers 403); revoked ones go.
    assert [(k.id, k.consumer_status) for k in default.keys] == [(blocked.id, "blocked")]


async def test_lists_are_sorted(db: AsyncSession, workspace: Workspace) -> None:
    service = await make_service(db, workspace.id)
    for prefix in ("/v1/b", "/v1/a", "/v1/c"):
        await make_route(db, workspace.id, service.id, path_prefix=prefix)
    consumer = await make_consumer(db, workspace.id)
    for _ in range(3):
        await make_api_key(db, workspace.id, consumer.id)

    [default] = (await _answer()).workspaces

    # The same version always gives the same JSON.
    assert [r.path_prefix for r in default.routes] == ["/v1/a", "/v1/b", "/v1/c"]
    assert [k.id for k in default.keys] == sorted(k.id for k in default.keys)


async def test_effective_limits(db: AsyncSession, workspace: Workspace) -> None:
    await make_workspace(db, slug="acme", rate_limit_per_min=10)

    acme = _by_slug(await _answer())["acme"]

    # Its own value, or the platform default: the gateway never needs the defaults.
    assert acme.limits.model_dump() == {"rate_limit_per_min": 10, "max_concurrent": 200}


async def test_each_workspace_holds_only_its_own(
    db: AsyncSession, workspace: Workspace, other_workspace: Workspace
) -> None:
    # Two workspaces may publish the same path; each sees only its own.
    ours = await make_service(db, workspace.id, name="ours")
    theirs = await make_service(db, other_workspace.id, name="theirs")
    mine = await make_route(db, workspace.id, ours.id, path_prefix="/v1/orders")
    their_route = await make_route(db, other_workspace.id, theirs.id, path_prefix="/v1/orders")

    by_slug = _by_slug(await _answer())

    assert [r.id for r in by_slug["default"].routes] == [mine.id]
    assert [r.id for r in by_slug["other"].routes] == [their_route.id]


# =====================================================================
# Full and incremental answers (decision 7)
# =====================================================================


async def test_a_change_in_one_workspace_answers_only_that_one(
    db: AsyncSession, workspace: Workspace, other_workspace: Workspace
) -> None:
    seq = await read_seq(engine)
    await make_service(db, other_workspace.id)

    answer = await _answer(since=seq)

    assert (answer.seq, answer.full) == (seq + 1, False)
    assert [w.slug for w in answer.workspaces] == ["other"]
    assert answer.deleted == []


async def test_several_changes_arrive_once_as_the_latest_state(
    db: AsyncSession, workspace: Workspace
) -> None:
    seq = await read_seq(engine)
    for name in ("a", "b", "c"):
        await make_service(db, workspace.id, name=name)

    [default] = (await _answer(since=seq)).workspaces

    assert [s.id for s in default.services] == sorted(s.id for s in default.services)
    assert len(default.services) == 3
    assert default.version == 4  # 1 + three changes


@pytest.mark.parametrize("status", [WorkspaceStatus.DELETED, WorkspaceStatus.PURGED])
async def test_a_deleted_workspace_is_listed_by_id(
    db: AsyncSession, workspace: Workspace, status: WorkspaceStatus
) -> None:
    acme = await make_workspace(db, slug="acme")
    seq = await read_seq(engine)
    await set_status(acme.id, status)

    answer = await _answer(since=seq)

    assert (answer.workspaces, answer.deleted) == ([], [acme.id])
    full = await _answer()
    assert [w.slug for w in full.workspaces] == ["default"]  # gone from a full answer
    assert full.deleted == []  # a full answer replaces everything anyway


async def test_a_suspended_workspace_stays_with_its_status(
    db: AsyncSession, workspace: Workspace
) -> None:
    # The gateway answers 403 workspace_suspended for it (MT.7).
    acme = await make_workspace(db, slug="acme")
    seq = await read_seq(engine)
    await set_status(acme.id, WorkspaceStatus.SUSPENDED)

    [changed] = (await _answer(since=seq)).workspaces

    assert (changed.slug, changed.status) == ("acme", WorkspaceStatus.SUSPENDED)


async def test_a_restored_workspace_comes_back_complete(
    db: AsyncSession, workspace: Workspace
) -> None:
    acme = await make_workspace(db, slug="acme")
    await make_service(db, acme.id)
    await set_status(acme.id, WorkspaceStatus.DELETED)
    seq = await read_seq(engine)
    await set_status(acme.id, WorkspaceStatus.ACTIVE)

    [restored] = (await _answer(since=seq)).workspaces

    assert (restored.slug, len(restored.services)) == ("acme", 1)


@pytest.mark.parametrize("since", [0, 10_000])
async def test_since_zero_or_from_the_future_is_full(workspace: Workspace, since: int) -> None:
    # 10,000: newer than the database's sequence, e.g. after a database reset.
    answer = await _answer(since=since)
    assert answer.full is True
    assert [w.slug for w in answer.workspaces] == ["default"]


async def test_the_workspaces_filter(db: AsyncSession, workspace: Workspace) -> None:
    # A dedicated gateway for some workspaces only.
    for slug in ("acme", "globex"):
        await make_workspace(db, slug=slug)
    seq = await read_seq(engine)
    for slug in ("acme", "globex"):
        await set_status((await _workspace_id(db, slug)), WorkspaceStatus.SUSPENDED)

    assert [w.slug for w in (await _answer(slugs=frozenset({"acme"}))).workspaces] == ["acme"]
    incremental = await _answer(since=seq, slugs=frozenset({"globex", "nope"}))
    assert [w.slug for w in incremental.workspaces] == ["globex"]


async def _workspace_id(db: AsyncSession, slug: str) -> uuid.UUID:
    found = await db.scalar(text("SELECT id FROM workspaces WHERE slug = :s"), {"s": slug})
    assert isinstance(found, uuid.UUID)
    return found


# ---- The per-(workspace, version) cache


async def test_an_unchanged_workspace_is_not_built_again(
    db: AsyncSession,
    workspace: Workspace,
    other_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[list[uuid.UUID]] = []
    real_build = gateway_crud._build

    async def counting_build(conn: Any, ids: list[uuid.UUID]) -> Any:
        built.append(sorted(ids))
        return await real_build(conn, ids)

    monkeypatch.setattr(gateway_crud, "_build", counting_build)
    cache: dict[uuid.UUID, WorkspaceConfigOut] = {}

    await read_answer(engine, since=0, slugs=None, cache=cache)
    await read_answer(engine, since=0, slugs=None, cache=cache)  # another gateway starting
    await make_service(db, other_workspace.id)
    answer = await read_answer(engine, since=0, slugs=None, cache=cache)

    assert built == [sorted([workspace.id, other_workspace.id]), [other_workspace.id]]
    assert len(_by_slug(answer)["other"].services) == 1  # the new version, not a stale one


async def test_deleted_workspaces_leave_the_cache(db: AsyncSession, workspace: Workspace) -> None:
    acme = await make_workspace(db, slug="acme")
    cache: dict[uuid.UUID, WorkspaceConfigOut] = {}
    await read_answer(engine, since=0, slugs=None, cache=cache)
    seq = await read_seq(engine)
    await set_status(acme.id, WorkspaceStatus.DELETED)

    await read_answer(engine, since=seq, slugs=None, cache=cache)

    assert acme.id not in cache


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
# GET /internal/config: 304 and long polling
# =====================================================================

URL = "/internal/config"


async def change_config(client: AsyncClient, auth_headers: dict[str, str], name: str) -> None:
    """An admin change through the real API (bumps the sequence, sends NOTIFY)."""
    body = {"name": name, "email": "dev@example.com"}
    assert (
        await client.post("/api/v1/workspaces/default/consumers", json=body, headers=auth_headers)
    ).status_code == 201


async def test_needs_the_internal_token(client: AsyncClient, notifier: ConfigNotifier) -> None:
    app.state.config_provider = ConfigProvider(engine, notifier)
    assert (await client.get(URL)).status_code == 401


async def test_first_call_is_full_with_an_etag(gateway: AsyncClient, workspace: Workspace) -> None:
    r = await gateway.get(URL)
    assert r.status_code == 200
    assert r.headers["ETag"] == '"2"'
    assert r.headers["Cache-Control"] == "no-cache"
    body = r.json()
    assert (body["seq"], body["full"], body["deleted"]) == (2, True, [])
    assert [w["slug"] for w in body["workspaces"]] == ["default"]


async def test_an_up_to_date_gateway_gets_304(gateway: AsyncClient, workspace: Workspace) -> None:
    r = await gateway.get(URL, params={"since": 2})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["ETag"] == '"2"'


async def test_the_answer_over_http_is_incremental(
    gateway: AsyncClient, auth_headers: dict[str, str], other_workspace: Workspace
) -> None:
    since = (await gateway.get(URL)).json()["seq"]
    await change_config(gateway, auth_headers, "mobile-app")  # in `default`

    body = (await gateway.get(URL, params={"since": since, "workspaces": "default, ,other"})).json()

    assert (body["seq"], body["full"]) == (since + 1, False)
    assert [w["slug"] for w in body["workspaces"]] == ["default"]


@pytest.mark.parametrize(("param", "value"), [("wait", -1), ("wait", 61), ("wait", "soon"),
                                              ("since", -1), ("since", "x")])  # fmt: skip
async def test_bad_query_values_are_422(gateway: AsyncClient, param: str, value: object) -> None:
    assert (await gateway.get(URL, params={param: value})).status_code == 422


async def test_long_poll_without_change_ends_with_304(
    gateway: AsyncClient, workspace: Workspace
) -> None:
    started = time.monotonic()
    r = await gateway.get(URL, params={"since": 2, "wait": 1})
    assert r.status_code == 304
    assert time.monotonic() - started >= 0.9  # it really held the request


async def test_long_poll_answers_the_moment_config_changes(
    gateway: AsyncClient, auth_headers: dict[str, str]
) -> None:
    waiting = asyncio.create_task(gateway.get(URL, params={"since": 2, "wait": 30}))
    await asyncio.sleep(0.3)
    assert not waiting.done()  # held open: nothing has changed yet

    changed_at = time.monotonic()
    await change_config(gateway, auth_headers, "mobile-app")
    r = await asyncio.wait_for(waiting, timeout=5)

    assert time.monotonic() - changed_at < 1  # not 30 seconds: woken by NOTIFY
    assert r.status_code == 200
    assert r.headers["ETag"] == '"3"'
    assert r.json()["seq"] == 3


async def test_missed_notify_is_still_answered_correctly(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # A notifier that never started: no NOTIFY will ever wake the request.
    deaf = ConfigNotifier(driver_dsn(get_settings().database_url))
    app.state.config_provider = ConfigProvider(engine, deaf)
    client.headers["X-Internal-Token"] = get_settings().internal_token

    waiting = asyncio.create_task(client.get(URL, params={"since": 2, "wait": 1}))
    await asyncio.sleep(0.2)
    await change_config(client, auth_headers, "mobile-app")
    r = await waiting
    # It slept the full second, but re-checked the database before answering.
    assert r.status_code == 200
    assert r.json()["seq"] == 3


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

    waiting = asyncio.create_task(gateway.get(URL, params={"since": 2, "wait": 30}))
    await asyncio.sleep(0.2)
    await change_config(gateway, auth_headers, "mobile-app")
    assert (await asyncio.wait_for(waiting, timeout=5)).status_code == 200


async def test_many_waiting_gateways_share_one_build(
    gateway: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = 0
    real_build = gateway_crud._build

    async def counting_build(*args: Any, **kwargs: Any) -> Any:
        nonlocal builds
        builds += 1
        return await real_build(*args, **kwargs)

    monkeypatch.setattr(gateway_crud, "_build", counting_build)

    waiting = [
        asyncio.create_task(gateway.get(URL, params={"since": 2, "wait": 30})) for _ in range(5)
    ]
    await asyncio.sleep(0.3)
    await change_config(gateway, auth_headers, "mobile-app")
    results = await asyncio.wait_for(asyncio.gather(*waiting), timeout=5)

    assert {r.status_code for r in results} == {200}
    assert {r.json()["seq"] for r in results} == {3}
    assert builds == 1  # five gateways woke up, Postgres built the workspace once


async def test_stopping_the_notifier_releases_waiting_requests(
    notifier: ConfigNotifier, workspace: Workspace
) -> None:
    provider = ConfigProvider(engine, notifier)
    waiting = asyncio.create_task(provider.get_changes(since=2, wait=30))
    await asyncio.sleep(0.2)
    started = time.monotonic()
    await notifier.stop()
    assert await asyncio.wait_for(waiting, timeout=5) is None  # nothing changed -> 304
    assert time.monotonic() - started < 1


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
