"""A workspace's life (decision 10): suspend, unsuspend, delete, restore, rename.

The guard tests (test_api_guards.py) check every endpoint in a suspended and
a deleted workspace; these check each transition, its refusals and its event.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEvent
from app.core.db import SessionLocal
from app.core.permissions import Role
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceStatus
from tests.factories import (
    make_consumer,
    make_member,
    make_route,
    make_service,
    make_user,
    make_workspace,
)
from tests.fakes import bearer, make_token
from tests.test_api_guards import set_status

WORKSPACES = "/api/v1/workspaces"
Headers = dict[str, str]


def _headers(user: User) -> Headers:
    assert user.auth_user_id is not None
    return bearer(make_token(user.auth_user_id, user.email))


@pytest.fixture
async def acme(db: AsyncSession) -> Workspace:
    return await make_workspace(db, slug="acme")


@pytest.fixture
async def owner(db: AsyncSession, acme: Workspace) -> Headers:
    """The headers of acme's Owner."""
    person = await make_user(db, email="olivia@acme.test")
    await make_member(db, acme.id, person.id, Role.OWNER)
    return _headers(person)


async def _workspace(slug: str = "acme") -> Workspace:
    async with SessionLocal() as session:  # fresh: no cached row
        return (await session.scalars(select(Workspace).where(Workspace.slug == slug))).one()


async def _events(action: str) -> list[AuditEvent]:
    async with SessionLocal() as session:
        return list(await session.scalars(select(AuditEvent).where(AuditEvent.action == action)))


async def _suspend(client: AsyncClient, headers: Headers, slug: str = "acme") -> Any:
    return await client.post(
        f"{WORKSPACES}/{slug}/suspend", json={"reason": "abuse"}, headers=headers
    )


async def _delete(client: AsyncClient, headers: Headers, confirm: str = "acme") -> Any:
    return await client.request(
        "DELETE", f"{WORKSPACES}/acme", json={"confirm": confirm}, headers=headers
    )


# ---- Suspend and unsuspend (Platform Admin)


async def test_suspend_and_unsuspend(
    client: AsyncClient, acme: Workspace, admin: User, mfa_headers: Headers
) -> None:
    r = await _suspend(client, mfa_headers)

    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["status_reason"]) == ("suspended", "abuse")
    [event] = await _events("workspace.suspended")
    # Platform-level: it outlives the workspace's own log.
    assert (event.workspace_id, event.target_id, event.target_name) == (None, acme.id, "acme")
    assert event.actor_type == "platform_admin"
    assert event.changes == {"status": ["active", "suspended"], "status_reason": [None, "abuse"]}

    r = await client.post(f"{WORKSPACES}/acme/unsuspend", headers=mfa_headers)

    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["status_reason"]) == ("active", None)
    [event] = await _events("workspace.unsuspended")
    assert event.changes == {"status": ["suspended", "active"], "status_reason": ["abuse", None]}


async def test_members_see_why_their_workspace_is_suspended(
    client: AsyncClient, acme: Workspace, owner: Headers, admin: User, mfa_headers: Headers
) -> None:
    await _suspend(client, mfa_headers)

    mine = (await client.get("/api/v1/me/workspaces", headers=owner)).json()["items"]
    assert [(w["slug"], w["status"], w["status_reason"]) for w in mine] == [
        ("acme", "suspended", "abuse")
    ]
    r = await client.get(f"{WORKSPACES}/acme", headers=owner)
    assert (r.status_code, r.json()["status"]) == (200, "suspended")


@pytest.mark.parametrize(
    ("status", "endpoint", "detail"),
    [
        (WorkspaceStatus.SUSPENDED, "suspend", "workspace_suspended"),
        (WorkspaceStatus.DELETED, "suspend", "workspace_deleted"),
        (WorkspaceStatus.PURGED, "suspend", "workspace_purged"),
        (WorkspaceStatus.ACTIVE, "unsuspend", "workspace_active"),
        (WorkspaceStatus.DELETED, "unsuspend", "workspace_deleted"),
        (WorkspaceStatus.ACTIVE, "restore", "workspace_active"),
        (WorkspaceStatus.SUSPENDED, "restore", "workspace_suspended"),
        (WorkspaceStatus.PURGED, "restore", "workspace_purged"),
    ],
)
async def test_a_transition_from_the_wrong_status_is_409(
    client: AsyncClient,
    acme: Workspace,
    admin: User,
    mfa_headers: Headers,
    status: WorkspaceStatus,
    endpoint: str,
    detail: str,
) -> None:
    await set_status(acme.id, status)
    body = {"reason": "abuse"} if endpoint == "suspend" else None

    r = await client.post(f"{WORKSPACES}/acme/{endpoint}", json=body, headers=mfa_headers)

    assert r.status_code == 409
    assert r.json() == {"detail": detail}
    assert (await _workspace()).status == status  # nothing changed
    async with SessionLocal() as session:
        changes = select(AuditEvent).where(AuditEvent.action != "workspace.created")
        assert list(await session.scalars(changes)) == []


@pytest.mark.parametrize("endpoint", ["suspend", "unsuspend", "restore"])
async def test_an_unknown_workspace_is_404(
    client: AsyncClient, admin: User, mfa_headers: Headers, endpoint: str
) -> None:
    r = await client.post(
        f"{WORKSPACES}/nope/{endpoint}", json={"reason": "abuse"}, headers=mfa_headers
    )
    assert r.status_code == 404


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501])
async def test_a_suspension_needs_a_reason(
    client: AsyncClient, acme: Workspace, admin: User, mfa_headers: Headers, reason: str
) -> None:
    r = await client.post(
        f"{WORKSPACES}/acme/suspend", json={"reason": reason}, headers=mfa_headers
    )
    assert r.status_code == 422


async def test_default_is_never_suspended_or_deleted(
    client: AsyncClient, workspace: Workspace, admin: User, mfa_headers: Headers
) -> None:
    # Requests without a subdomain go to `default`; the database refuses it too.
    r = await _suspend(client, mfa_headers, slug="default")
    assert (r.status_code, r.json()) == (409, {"detail": "default_workspace"})

    r = await client.request(
        "DELETE", f"{WORKSPACES}/default", json={"confirm": "default"}, headers=mfa_headers
    )
    assert (r.status_code, r.json()) == (409, {"detail": "default_workspace"})
    assert (await _workspace("default")).status == WorkspaceStatus.ACTIVE


async def test_two_suspensions_at_once_make_one(
    client: AsyncClient, acme: Workspace, admin: User, mfa_headers: Headers
) -> None:
    # The row lock runs them one after the other; the second sees the first.
    answers = await asyncio.gather(*(_suspend(client, mfa_headers) for _ in range(2)))

    assert sorted(r.status_code for r in answers) == [200, 409]
    assert len(await _events("workspace.suspended")) == 1


# ---- Delete (Owner or Platform Admin) and restore (Platform Admin)


async def test_an_owner_deletes_their_workspace(
    client: AsyncClient, acme: Workspace, owner: Headers, admin: User, mfa_headers: Headers
) -> None:
    r = await _delete(client, owner)

    assert r.status_code == 204
    # Gone for its members at once...
    assert (await client.get(f"{WORKSPACES}/acme", headers=owner)).status_code == 404
    mine = await client.get("/api/v1/me/workspaces", headers=owner)
    assert mine.json()["items"] == []
    # ...but the Platform Admin still sees it, restorable for 30 days.
    listed = await client.get(WORKSPACES, params={"status": "deleted"}, headers=mfa_headers)
    [deleted] = listed.json()["items"]
    assert (deleted["slug"], deleted["status"]) == ("acme", "deleted")
    deleted_at = datetime.fromisoformat(deleted["deleted_at"])
    assert datetime.fromisoformat(deleted["purge_after"]) - deleted_at == timedelta(days=30)
    assert abs(datetime.now(UTC) - deleted_at) < timedelta(minutes=1)
    [event] = await _events("workspace.deleted")
    assert (event.workspace_id, event.actor_type) == (None, "user")  # platform-level
    assert event.changes["status"] == ["active", "deleted"]


async def test_deleting_needs_the_slug_typed_again(
    client: AsyncClient, acme: Workspace, owner: Headers
) -> None:
    for wrong in ("ACME", "acme ", "globex"):
        r = await _delete(client, owner, confirm=wrong)
        assert r.status_code == 422, wrong
    r = await client.request("DELETE", f"{WORKSPACES}/acme", headers=owner)  # no body
    assert r.status_code == 422
    assert (await _workspace()).status == WorkspaceStatus.ACTIVE
    assert await _events("workspace.deleted") == []


async def test_a_platform_admin_deletes_a_suspended_workspace(
    client: AsyncClient, acme: Workspace, owner: Headers, admin: User, mfa_headers: Headers
) -> None:
    await _suspend(client, mfa_headers)
    assert (await _delete(client, owner)).status_code == 403  # read-only for members

    r = await _delete(client, mfa_headers)

    assert r.status_code == 204
    workspace = await _workspace()
    assert (workspace.status, workspace.status_reason) == (WorkspaceStatus.DELETED, None)


async def test_restore_brings_it_back_active(
    client: AsyncClient,
    db: AsyncSession,
    acme: Workspace,
    owner: Headers,
    admin: User,
    mfa_headers: Headers,
) -> None:
    service = await make_service(db, acme.id)
    await _delete(client, owner)

    r = await client.post(f"{WORKSPACES}/acme/restore", headers=mfa_headers)

    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["deleted_at"], body["purge_after"]) == ("active", None, None)
    # Everything it had is there again for its members.
    services = await client.get(f"{WORKSPACES}/acme/services", headers=owner)
    assert [s["id"] for s in services.json()["items"]] == [str(service.id)]
    [event] = await _events("workspace.restored")
    assert event.workspace_id is None
    assert event.changes["status"] == ["deleted", "active"]


async def test_restore_after_the_period_is_refused(
    client: AsyncClient, acme: Workspace, admin: User, mfa_headers: Headers
) -> None:
    # Past purge_after the purge job may be deleting it already.
    await set_status(acme.id, WorkspaceStatus.DELETED)
    async with SessionLocal() as session:
        past = datetime.now(UTC) - timedelta(seconds=1)
        await session.execute(
            update(Workspace)
            .where(Workspace.id == acme.id)
            .values(deleted_at=past - timedelta(days=30), purge_after=past)
        )
        await session.commit()

    r = await client.post(f"{WORKSPACES}/acme/restore", headers=mfa_headers)

    assert (r.status_code, r.json()) == (409, {"detail": "restore_period_over"})
    assert (await _workspace()).status == WorkspaceStatus.DELETED


@pytest.mark.parametrize("status", [WorkspaceStatus.DELETED, WorkspaceStatus.PURGED])
async def test_a_deleted_workspaces_slug_is_never_taken_again(
    client: AsyncClient,
    acme: Workspace,
    admin: User,
    mfa_headers: Headers,
    status: WorkspaceStatus,
) -> None:
    # The slug is the gateway subdomain: a new tenant there would receive the
    # old one's clients.
    await set_status(acme.id, status)
    body = {"name": "New Acme", "slug": "acme", "owner_email": admin.email}

    r = await client.post(WORKSPACES, json=body, headers=mfa_headers)

    assert r.status_code == 409


async def test_the_platform_list_filters_by_status(
    client: AsyncClient, workspace: Workspace, acme: Workspace, admin: User, mfa_headers: Headers
) -> None:
    await set_status(acme.id, WorkspaceStatus.SUSPENDED)

    for status, expected in (("suspended", ["acme"]), ("active", ["default"]), ("purged", [])):
        r = await client.get(WORKSPACES, params={"status": status}, headers=mfa_headers)
        assert [w["slug"] for w in r.json()["items"]] == expected
    r = await client.get(WORKSPACES, params={"status": "archived"}, headers=mfa_headers)
    assert r.status_code == 422


# ---- Rename (Owner) and the details members see


async def test_an_owner_renames_their_workspace(
    client: AsyncClient, acme: Workspace, owner: Headers
) -> None:
    r = await client.patch(f"{WORKSPACES}/acme", json={"name": "  Acme Corp  "}, headers=owner)

    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["slug"]) == ("Acme Corp", "acme")
    [event] = await _events("workspace.update")
    # A workspace-level event: its members see it in their own log.
    assert event.workspace_id == acme.id
    assert event.changes == {"name": ["Acme", "Acme Corp"]}

    r = await client.patch(f"{WORKSPACES}/acme", json={"name": "Acme Corp"}, headers=owner)
    assert r.status_code == 200
    assert len(await _events("workspace.update")) == 1  # nothing changed, no event


@pytest.mark.parametrize("body", [{"name": None}, {"name": ""}, {"slug": "other"}])
async def test_a_rename_needs_a_name_and_never_moves_the_slug(
    client: AsyncClient, acme: Workspace, owner: Headers, body: dict[str, Any]
) -> None:
    r = await client.patch(f"{WORKSPACES}/acme", json=body, headers=owner)
    assert r.status_code in (200, 422)
    assert ((await _workspace()).name, (await _workspace()).slug) == ("Acme", "acme")


async def test_details_show_limits_and_usage(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: Headers
) -> None:
    service = await make_service(db, acme.id)
    await make_route(db, acme.id, service.id)
    await make_consumer(db, acme.id)

    r = await client.get(f"{WORKSPACES}/acme", headers=owner)

    body = r.json()
    assert body["limits"] == {
        "rate_limit_per_min": 6000, "max_concurrent": 200,
        "max_services": 100, "max_routes": 500, "max_consumers": 5000,
    }  # fmt: skip
    assert set(body["limit_overrides"].values()) == {None}
    assert body["usage"] == {"services": 1, "routes": 1, "consumers": 1}
    assert body["status"] == "active"
