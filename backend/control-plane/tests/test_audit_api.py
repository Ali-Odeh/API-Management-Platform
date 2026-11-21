"""API tests for the audit log: a workspace's log and the whole platform's."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import pool, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.audit.models import AuditEvent
from app.core.permissions import Role
from app.users.models import User
from app.workspaces.models import Workspace
from tests.conftest import OWNER_URL
from tests.factories import make_member, make_user, make_workspace
from tests.fakes import bearer, make_token

WORKSPACE_LOG = "/api/v1/workspaces/acme/audit-events"
PLATFORM_LOG = "/api/v1/audit-events"
Headers = dict[str, str]


def _headers(user: User, *, mfa: bool = False) -> Headers:
    assert user.auth_user_id is not None
    return bearer(make_token(user.auth_user_id, user.email, aal="aal2" if mfa else "aal1"))


async def _event(
    db: AsyncSession, workspace_id: uuid.UUID | None, action: str, **fields: Any
) -> AuditEvent:
    fields.setdefault("actor_type", "user")
    fields.setdefault("target_type", action.split(".")[0])
    event = AuditEvent(workspace_id=workspace_id, action=action, changes={}, **fields)
    db.add(event)
    await db.commit()
    return event


@pytest.fixture
async def acme(db: AsyncSession) -> Workspace:
    return await make_workspace(db, slug="acme")


@pytest.fixture
async def owner(db: AsyncSession, acme: Workspace) -> User:
    user = await make_user(db, email="owner@acme.test")
    await make_member(db, acme.id, user.id, Role.OWNER)
    return user


async def _as_owner(**values: Any) -> None:
    """Change an event as the owner: the API's own login may never UPDATE one."""
    event_id = values.pop("event_id")
    owner = create_async_engine(OWNER_URL, poolclass=pool.NullPool)
    async with owner.begin() as conn:
        await conn.execute(update(AuditEvent).where(AuditEvent.id == event_id).values(**values))
    await owner.dispose()


async def _set_time(event: AuditEvent, when: datetime) -> None:
    await _as_owner(event_id=event.id, created_at=when)


async def test_a_workspace_log_shows_only_that_workspace(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User
) -> None:
    globex = await make_workspace(db, slug="globex")
    ours = await _event(db, acme.id, "service.create")
    by_platform = await _event(db, acme.id, "route.update", actor_type="platform_admin")
    await _event(db, globex.id, "service.create")  # another workspace
    await _event(db, None, "workspace.created")  # platform-level

    r = await client.get(WORKSPACE_LOG, headers=_headers(owner))

    assert r.status_code == 200
    assert r.json()["total"] == 2
    ids = {e["id"] for e in r.json()["items"]}
    assert ids == {str(ours.id), str(by_platform.id)}  # the Platform Admin's change included


async def test_newest_first_and_paged(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User
) -> None:
    start = datetime(2026, 11, 1, tzinfo=UTC)
    for day in range(3):
        event = await _event(db, acme.id, f"service.update{day}")
        await _set_time(event, start + timedelta(days=day))

    r = await client.get(WORKSPACE_LOG, params={"page_size": 2}, headers=_headers(owner))

    assert r.json()["total"] == 3
    assert [e["action"] for e in r.json()["items"]] == ["service.update2", "service.update1"]


async def test_filters(client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User) -> None:
    start = datetime(2026, 11, 1, tzinfo=UTC)
    events = [
        await _event(db, acme.id, "key.revoke", actor_email="ann@acme.test"),
        await _event(db, acme.id, "key.create", actor_email="bob@acme.test"),
        await _event(db, acme.id, "service.delete", actor_email="ann@acme.test"),
    ]
    for day, event in enumerate(events):
        await _set_time(event, start + timedelta(days=day))

    async def actions(**params: str) -> list[str]:
        r = await client.get(WORKSPACE_LOG, params=params, headers=_headers(owner))
        assert r.status_code == 200, r.text
        return sorted(e["action"] for e in r.json()["items"])

    assert await actions(actor="ANN@acme.test") == ["key.revoke", "service.delete"]
    assert await actions(action="key.create") == ["key.create"]
    assert await actions(target_type="key") == ["key.create", "key.revoke"]
    assert await actions(**{"from": "2026-11-02T00:00:00Z"}) == ["key.create", "service.delete"]
    assert await actions(**{"to": "2026-11-02T00:00:00Z"}) == ["key.revoke"]


async def test_bad_filter_is_422(client: AsyncClient, acme: Workspace, owner: User) -> None:
    r = await client.get(WORKSPACE_LOG, params={"from": "yesterday"}, headers=_headers(owner))
    assert r.status_code == 422


async def test_the_platform_log_shows_everything(
    client: AsyncClient, db: AsyncSession, acme: Workspace, mfa_headers: Headers
) -> None:
    globex = await make_workspace(db, slug="globex")
    ours = await _event(db, acme.id, "service.create")
    theirs = await _event(db, globex.id, "service.create")
    platform = await _event(db, None, "workspace.created")

    everything = await client.get(PLATFORM_LOG, headers=mfa_headers)
    one = await client.get(
        PLATFORM_LOG, params={"workspace_id": str(globex.id)}, headers=mfa_headers
    )

    listed = {e["id"] for e in everything.json()["items"]}
    assert {str(ours.id), str(theirs.id), str(platform.id)} <= listed  # (setup adds `default`'s)
    assert [e["id"] for e in one.json()["items"]] == [str(theirs.id)]


async def test_an_event_reads_back_whole(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User
) -> None:
    event = await _event(
        db,
        acme.id,
        "service.update",
        actor_user_id=owner.id,
        actor_email=owner.email,
        target_id=uuid.uuid4(),
        target_name="orders",
        request_id="req-1",
        ip="10.0.0.7",
        user_agent="curl/8",
    )
    await _as_owner(event_id=event.id, changes={"timeout_ms": [5000, 10000]})

    [item] = (await client.get(WORKSPACE_LOG, headers=_headers(owner))).json()["items"]

    assert item["changes"] == {"timeout_ms": [5000, 10000]}
    assert (item["actor_email"], item["target_name"]) == (owner.email, "orders")
    assert (item["request_id"], item["ip"], item["user_agent"]) == ("req-1", "10.0.0.7", "curl/8")
