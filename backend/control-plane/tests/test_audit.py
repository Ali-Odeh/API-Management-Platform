"""The audit log: change records, append-only privileges, request ids, and the
events outside the endpoints (startup, first sign-in, scripts)."""

import os
import uuid
from datetime import UTC, datetime
from enum import Enum

import asyncpg
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message, Receive, Scope, Send

from app.audit import crud as audit_crud
from app.audit.changes import created, deleted, snapshot, updated
from app.audit.context import AuditContext
from app.audit.models import AuditEvent
from app.core.db import driver_dsn
from app.core.request_id import RequestIdMiddleware
from app.gotrue import client as gotrue_client
from app.main import app
from app.users import crud as users_crud
from app.users.models import User
from app.workspaces.models import Workspace
from tests.factories import make_consumer, make_route, make_service, make_user
from tests.fakes import FakeGoTrue, bearer, make_token

Headers = dict[str, str]
SERVICES = "/api/v1/workspaces/default/services"


async def _ids(db: AsyncSession) -> set[uuid.UUID]:
    """The events already there, e.g. written by the fixtures' own setup."""
    return set(await db.scalars(select(AuditEvent.id)))


async def _events(db: AsyncSession, seen: set[uuid.UUID] | None = None) -> list[AuditEvent]:
    """The events written since `seen` (all of them without it), oldest first."""
    query = select(AuditEvent).order_by(AuditEvent.created_at, AuditEvent.action)
    if seen:
        query = query.where(AuditEvent.id.not_in(seen))
    return list(await db.scalars(query.execution_options(populate_existing=True)))


# ---- Changes: {"field": [before, after]}, changed fields only


class _Color(Enum):
    RED = "red"


def test_changes_hold_only_what_changed() -> None:
    before = {"timeout_ms": 5000, "name": "orders"}
    after = {"timeout_ms": 10000, "name": "orders"}
    assert updated(before, after) == {"timeout_ms": [5000, 10000]}
    assert created({"a": 1}) == {"a": [None, 1]}
    assert deleted({"a": 1}) == {"a": [1, None]}


def test_snapshot_makes_values_json_friendly() -> None:
    some_id = uuid.uuid4()
    when = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)

    class Row:
        id = some_id
        at = when
        color = _Color.RED
        methods = ("GET", _Color.RED)

    assert snapshot(Row(), ["id", "at", "color", "methods"]) == {
        "id": str(some_id),
        "at": "2026-11-21T12:00:00+00:00",
        "color": "red",
        "methods": ["GET", "red"],
    }


# ---- Append-only: what each database login may do (decision 11)


async def _as(env: str) -> asyncpg.Connection:
    return await asyncpg.connect(driver_dsn(os.environ[env]))


_INSERT = (
    "INSERT INTO audit_events (id, actor_type, action, target_type, changes) "
    "VALUES (gen_random_uuid(), 'system', 'test.event', 'test', '{}')"
)


async def test_the_api_login_may_only_add_and_read_events() -> None:
    conn = await _as("DATABASE_URL")
    try:
        await conn.execute(_INSERT)
        assert await conn.fetchval("SELECT count(*) FROM audit_events") == 1
        for forbidden in (
            "UPDATE audit_events SET action = 'rewritten'",
            "DELETE FROM audit_events",
            "TRUNCATE audit_events",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(forbidden)
    finally:
        await conn.close()


async def test_the_retention_login_may_only_read_and_delete_events() -> None:
    app_conn = await _as("DATABASE_URL")
    await app_conn.execute(_INSERT)
    await app_conn.close()

    conn = await _as("AUDIT_RETENTION_DATABASE_URL")
    try:
        assert await conn.execute("DELETE FROM audit_events") == "DELETE 1"
        for forbidden in (
            _INSERT,
            "UPDATE audit_events SET action = 'rewritten'",
            "SELECT count(*) FROM services",  # nothing else of the platform's
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(forbidden)
    finally:
        await conn.close()


# ---- Request ids: from the client when safe, else new; always echoed


async def test_a_safe_request_id_is_kept_and_recorded(
    client: AsyncClient, db: AsyncSession, auth_headers: Headers
) -> None:
    body = {"name": "orders", "upstream_url": "http://mock-backend:9000"}
    headers = {**auth_headers, "X-Request-ID": "trace-42.a_b"}
    seen = await _ids(db)

    r = await client.post(SERVICES, json=body, headers=headers)

    assert r.headers["X-Request-ID"] == "trace-42.a_b"
    [event] = await _events(db, seen)
    assert event.request_id == "trace-42.a_b"
    assert str(event.ip) == "127.0.0.1"  # the in-memory test client's address


@pytest.mark.parametrize("sent", [None, "", "x" * 129, "bad id", "new\nline", "a;b"])
async def test_an_unsafe_or_missing_request_id_is_replaced(
    client: AsyncClient, sent: str | None
) -> None:
    headers = {"X-Request-ID": sent} if sent is not None else {}
    r = await client.get("/health", headers=headers)
    new_id = r.headers["X-Request-ID"]
    assert new_id != sent and len(new_id) == 32  # a fresh uuid4, as hex


# ---- A request that changes nothing writes no event


@pytest.mark.parametrize(
    ("kind", "body"),
    [
        ("services", {"timeout_ms": 5000}),
        ("routes", {"methods": ["GET", "POST"]}),
        ("consumers", {"rate_limit_per_min": 60}),
    ],
)
async def test_unchanged_patch_writes_no_event(
    client: AsyncClient,
    db: AsyncSession,
    auth_headers: Headers,
    workspace: Workspace,
    kind: str,
    body: dict[str, object],
) -> None:
    service = await make_service(db, workspace.id, timeout_ms=5000)
    ids = {
        "services": service.id,
        "routes": (await make_route(db, workspace.id, service.id, methods=["GET", "POST"])).id,
        "consumers": (await make_consumer(db, workspace.id, rate_limit_per_min=60)).id,
    }
    seen = await _ids(db)
    url = f"/api/v1/workspaces/default/{kind}/{ids[kind]}"

    r = await client.patch(url, json=body, headers=auth_headers)

    assert r.status_code == 200
    assert await _events(db, seen) == []


async def test_revoking_twice_writes_one_event(
    client: AsyncClient, db: AsyncSession, auth_headers: Headers, workspace: Workspace
) -> None:
    consumer = await make_consumer(db, workspace.id)
    keys = f"/api/v1/workspaces/default/consumers/{consumer.id}/keys"
    seen = await _ids(db)
    key_id = (await client.post(keys, json={"name": "k"}, headers=auth_headers)).json()["id"]
    for _ in range(2):
        r = await client.delete(f"/api/v1/workspaces/default/keys/{key_id}", headers=auth_headers)
        assert r.status_code == 204
    assert [e.action for e in await _events(db, seen)] == ["key.create", "key.revoke"]


async def test_repeated_disable_writes_one_event(
    client: AsyncClient, db: AsyncSession, mfa_headers: Headers
) -> None:
    person = await make_user(db, email="dev@example.com")
    seen = await _ids(db)
    for _ in range(2):
        r = await client.patch(
            f"/api/v1/users/{person.id}", json={"is_active": False}, headers=mfa_headers
        )
        assert r.status_code == 200
    assert [e.action for e in await _events(db, seen)] == ["user.disable"]


async def test_a_disable_kept_after_a_gotrue_failure_keeps_its_event(
    client: AsyncClient, db: AsyncSession, admin: User, mfa_headers: Headers, gotrue: FakeGoTrue
) -> None:
    # The disable is saved even though GoTrue failed (502): so is its event.
    person = await make_user(db, email="dev@example.com")
    gotrue.failing = True
    seen = await _ids(db)

    r = await client.patch(
        f"/api/v1/users/{person.id}", json={"is_active": False}, headers=mfa_headers
    )

    assert r.status_code == 502
    [event] = await _events(db, seen)
    assert (event.action, event.target_id, event.workspace_id) == ("user.disable", person.id, None)
    assert event.changes == {"is_active": [True, False]}
    assert (event.actor_type, event.actor_user_id) == ("platform_admin", admin.id)


# ---- Events outside the endpoints


async def test_a_first_request_records_the_new_user(client: AsyncClient, db: AsyncSession) -> None:
    auth_user_id = uuid.uuid4()
    r = await client.get("/api/v1/me", headers=bearer(make_token(auth_user_id, "new@example.com")))

    [event] = await _events(db)
    assert (event.action, event.target_id) == ("user.create", uuid.UUID(r.json()["id"]))
    assert (event.actor_type, event.actor_user_id) == ("user", event.target_id)  # by themself
    assert event.changes["auth_user_id"] == [None, str(auth_user_id)]


async def test_startup_records_what_it_creates_as_system(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gotrue_client, "create_gotrue_admin", lambda settings: FakeGoTrue())

    for _ in range(2):  # a restart changes nothing, so records nothing
        async with app.router.lifespan_context(app):
            pass

    events = await _events(db)
    assert sorted(e.action for e in events) == ["member.add", "user.create", "workspace.created"]
    assert {e.actor_type for e in events} == {"system"}
    default = await db.scalar(select(Workspace.id).where(Workspace.slug == "default"))
    by_action = {e.action: e for e in events}
    assert by_action["member.add"].workspace_id == default
    assert by_action["workspace.created"].workspace_id is None  # a platform-level event


async def test_relinking_is_recorded(db: AsyncSession, gotrue: FakeGoTrue) -> None:
    person = await make_user(db, email="boss@example.com")
    old = person.auth_user_id
    new = gotrue.add_account("boss@example.com")

    await users_crud.relink_user(db, gotrue, email="boss@example.com", audit=AuditContext.system())

    [event] = await _events(db)
    assert (event.action, event.actor_type) == ("user.relink", "system")
    assert event.changes == {"auth_user_id": [str(old), str(new)]}


def test_an_address_that_is_no_ip_is_not_stored() -> None:
    assert audit_crud._parse_ip("unix:/run/app.sock") is None
    assert audit_crud._parse_ip(None) is None
    assert str(audit_crud._parse_ip("::1")) == "::1"


async def test_relinking_to_the_same_account_records_nothing(
    db: AsyncSession, gotrue: FakeGoTrue
) -> None:
    person = await make_user(db, email="boss@example.com")
    assert person.auth_user_id is not None
    gotrue.accounts["boss@example.com"] = person.auth_user_id

    await users_crud.relink_user(db, gotrue, email="boss@example.com", audit=AuditContext.system())

    assert await _events(db) == []


async def test_the_request_id_middleware_lets_other_traffic_through() -> None:
    # Lifespan and websocket messages have no headers to read or answer.
    seen: list[Scope] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)

    async def no_receive() -> Message:
        return {"type": "lifespan.startup"}

    async def no_send(message: Message) -> None:
        return None

    scope: Scope = {"type": "lifespan"}
    await RequestIdMiddleware(inner)(scope, no_receive, no_send)
    assert seen == [{"type": "lifespan"}]  # untouched: no request id state added
