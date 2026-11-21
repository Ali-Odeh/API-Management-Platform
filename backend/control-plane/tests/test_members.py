"""Members and owners (decisions 8 and 9): adding, inviting, roles, removing,
leaving, and the rule that a workspace never loses its last active Owner."""

import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEvent
from app.core.permissions import Role
from app.main import app
from app.members import crud as members_crud
from app.members.models import WorkspaceMember
from app.users.models import User
from app.workspaces.models import Workspace
from tests.factories import make_member, make_user, make_workspace
from tests.fakes import FakeGoTrue, bearer, make_token

MEMBERS = "/api/v1/workspaces/acme/members"
LEAVE = "/api/v1/me/workspaces/acme"
Headers = dict[str, str]


def _headers(user: User, *, mfa: bool = False) -> Headers:
    assert user.auth_user_id is not None
    return bearer(make_token(user.auth_user_id, user.email, aal="aal2" if mfa else "aal1"))


@pytest.fixture
async def acme(db: AsyncSession) -> Workspace:
    return await make_workspace(db, slug="acme")


async def _member(db: AsyncSession, acme: Workspace, email: str, role: Role, **fields: Any) -> User:
    user = await make_user(db, email=email, **fields)
    await make_member(db, acme.id, user.id, role)
    return user


@pytest.fixture
async def owner(db: AsyncSession, acme: Workspace) -> User:
    return await _member(db, acme, "owner@acme.test", Role.OWNER)


async def _roles(db: AsyncSession, acme: Workspace) -> dict[str, str]:
    rows = await db.execute(
        select(User.email, WorkspaceMember.role)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == acme.id)
        .execution_options(populate_existing=True)
    )
    return dict(rows.tuples().all())


async def _actions(db: AsyncSession) -> list[str]:
    rows = await db.scalars(select(AuditEvent.action).where(AuditEvent.target_type == "member"))
    return sorted(rows)


# ---- List


async def test_list_shows_owners_first_with_their_person(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User
) -> None:
    viewer = await _member(db, acme, "a-viewer@acme.test", Role.VIEWER, is_active=False)

    r = await client.get(MEMBERS, headers=_headers(owner))

    assert r.json()["total"] == 2
    first, second = r.json()["items"]
    assert (first["email"], first["role"]) == ("owner@acme.test", "owner")
    assert (second["user_id"], second["role"], second["is_active"]) == (
        str(viewer.id),
        "viewer",
        False,
    )


# ---- Add (decision 8)


async def test_a_known_user_becomes_a_member_at_once(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User, gotrue: FakeGoTrue
) -> None:
    known = await make_user(db, email="known@example.com")

    r = await client.post(
        MEMBERS, json={"email": "Known@Example.com", "role": "api_admin"}, headers=_headers(owner)
    )

    assert r.status_code == 201
    assert (r.json()["user_id"], r.json()["role"], r.json()["invited"]) == (
        str(known.id),
        "api_admin",
        False,
    )
    assert r.json()["added_by"] == str(owner.id)
    assert gotrue.invited == []  # no email for someone who has an account
    assert await _actions(db) == ["member.add"]


async def test_a_new_person_is_invited_and_a_member_from_their_first_login(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User, gotrue: FakeGoTrue
) -> None:
    r = await client.post(
        MEMBERS, json={"email": "new@example.com", "role": "viewer"}, headers=_headers(owner)
    )

    assert r.status_code == 201 and r.json()["invited"] is True
    assert gotrue.invited == ["new@example.com"]
    # Their first login (a token for the invited account) finds the workspace.
    invitee = bearer(make_token(gotrue.accounts["new@example.com"], "new@example.com"))
    mine = (await client.get("/api/v1/me/workspaces", headers=invitee)).json()["items"]
    assert [(w["slug"], w["role"]) for w in mine] == [("acme", "viewer")]
    [event] = (await db.scalars(select(AuditEvent).where(AuditEvent.action == "member.add"))).all()
    assert event.changes == {"role": [None, "viewer"], "invited": [None, True]}


async def test_an_existing_gotrue_account_is_bound_not_invited_again(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User, gotrue: FakeGoTrue
) -> None:
    account = gotrue.add_account("pending@example.com")  # e.g. invited, never logged in

    r = await client.post(
        MEMBERS, json={"email": "pending@example.com", "role": "viewer"}, headers=_headers(owner)
    )

    assert r.status_code == 201 and r.json()["invited"] is False
    assert gotrue.invited == []
    user = await db.get(User, uuid.UUID(r.json()["user_id"]))
    assert user is not None and user.auth_user_id == account


class _AccountAppearsMeanwhile(FakeGoTrue):
    """Another process makes the account between our lookup and our invitation."""

    async def invite_user(self, email: str) -> uuid.UUID | None:
        self.add_account(email)
        return None


class _NoAccountAnywhere(FakeGoTrue):
    """GoTrue says the email is taken, yet no lookup finds it."""

    async def invite_user(self, email: str) -> uuid.UUID | None:
        return None


@pytest.mark.parametrize(
    ("fake", "status", "invited"),
    [(_AccountAppearsMeanwhile, 201, False), (_NoAccountAnywhere, 502, None)],
)
async def test_an_invitation_that_races_another_process(
    client: AsyncClient,
    acme: Workspace,
    owner: User,
    fake: type[FakeGoTrue],
    status: int,
    invited: bool | None,
) -> None:
    app.state.gotrue = fake()
    r = await client.post(
        MEMBERS, json={"email": "new@example.com", "role": "viewer"}, headers=_headers(owner)
    )
    assert r.status_code == status
    if invited is not None:
        assert r.json()["invited"] is invited  # we sent no email: GoTrue had the account


@pytest.mark.parametrize(
    ("email", "status", "detail"),
    [
        ("owner@acme.test", 409, "Already a member of this workspace"),
        ("disabled@example.com", 422, "This user is disabled"),
    ],
)
async def test_add_refusals(
    client: AsyncClient,
    db: AsyncSession,
    acme: Workspace,
    owner: User,
    email: str,
    status: int,
    detail: str,
) -> None:
    await make_user(db, email="disabled@example.com", is_active=False)
    r = await client.post(MEMBERS, json={"email": email, "role": "viewer"}, headers=_headers(owner))
    assert r.status_code == status
    assert r.json() == {"detail": detail}


@pytest.mark.parametrize("body", [{"email": "x@example.com", "role": "admin"}, {"role": "viewer"}])
async def test_invalid_add_is_422(
    client: AsyncClient, acme: Workspace, owner: User, body: dict[str, str]
) -> None:
    assert (await client.post(MEMBERS, json=body, headers=_headers(owner))).status_code == 422


async def test_gotrue_down_adds_nobody(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User, gotrue: FakeGoTrue
) -> None:
    gotrue.failing = True
    r = await client.post(
        MEMBERS, json={"email": "new@example.com", "role": "viewer"}, headers=_headers(owner)
    )
    assert r.status_code == 502
    assert await db.scalar(select(User).where(User.email == "new@example.com")) is None


async def test_the_same_new_person_added_twice_at_once(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User
) -> None:
    globex = await make_workspace(db, slug="globex")
    await make_member(db, globex.id, owner.id, Role.OWNER)
    body = {"email": "new@example.com", "role": "viewer"}

    results = await asyncio.gather(
        client.post(MEMBERS, json=body, headers=_headers(owner)),
        client.post("/api/v1/workspaces/globex/members", json=body, headers=_headers(owner)),
    )

    # One person either way: the second waits, then reuses or is told to retry.
    assert sorted(r.status_code for r in results) in ([201, 201], [201, 409])
    count = await db.scalar(select(func.count()).where(User.email == "new@example.com"))
    assert count == 1


async def test_an_account_linked_under_another_email_is_409(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User, gotrue: FakeGoTrue
) -> None:
    # The person changed their email in GoTrue; our row still has the old one.
    # GoTrue's id is taken, so they can't become a second user: 409, not 500.
    account = gotrue.add_account("renamed@example.com")
    await make_user(db, email="old-name@example.com", auth_user_id=account)

    r = await client.post(
        MEMBERS, json={"email": "renamed@example.com", "role": "viewer"}, headers=_headers(owner)
    )

    assert r.status_code == 409
    assert "already linked" in r.json()["detail"]
    count = await db.scalar(select(func.count()).where(WorkspaceMember.workspace_id == acme.id))
    assert count == 1  # only the Owner


# ---- Change role (decision 9)


async def test_an_owner_changes_a_role(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User
) -> None:
    viewer = await _member(db, acme, "viewer@acme.test", Role.VIEWER)

    r = await client.patch(
        f"{MEMBERS}/{viewer.id}", json={"role": "api_admin"}, headers=_headers(owner)
    )

    assert r.status_code == 200 and r.json()["role"] == "api_admin"
    [event] = (await db.scalars(select(AuditEvent))).all()
    assert (event.action, event.changes) == (
        "member.role_change",
        {"role": ["viewer", "api_admin"]},
    )


async def test_nobody_changes_their_own_role(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User
) -> None:
    await _member(db, acme, "second@acme.test", Role.OWNER)
    r = await client.patch(
        f"{MEMBERS}/{owner.id}", json={"role": "viewer"}, headers=_headers(owner)
    )
    assert r.status_code == 409
    assert r.json() == {"detail": "You can't change your own role"}


async def test_another_owner_can_be_demoted(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User
) -> None:
    second = await _member(db, acme, "second@acme.test", Role.OWNER)
    r = await client.patch(
        f"{MEMBERS}/{second.id}", json={"role": "viewer"}, headers=_headers(owner)
    )
    assert r.status_code == 200
    assert await _roles(db, acme) == {"owner@acme.test": "owner", "second@acme.test": "viewer"}


async def test_the_last_active_owner_cant_be_demoted(
    client: AsyncClient, db: AsyncSession, acme: Workspace
) -> None:
    # Two owners, but one is disabled: it can't manage anything, so it doesn't count.
    await _member(db, acme, "disabled@acme.test", Role.OWNER, is_active=False)
    last = await _member(db, acme, "last@acme.test", Role.OWNER)
    admin = await make_user(db, email="pa@example.com", is_platform_admin=True)

    # Not even the Platform Admin: the safety net follows the rule too.
    r = await client.patch(
        f"{MEMBERS}/{last.id}", json={"role": "viewer"}, headers=_headers(admin, mfa=True)
    )

    assert r.status_code == 409
    assert r.json() == {"detail": "last_owner"}
    assert (await _roles(db, acme))["last@acme.test"] == "owner"


async def test_unchanged_or_empty_role_change_changes_nothing(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User
) -> None:
    viewer = await _member(db, acme, "viewer@acme.test", Role.VIEWER)
    for body in ({}, {"role": "viewer"}):
        r = await client.patch(f"{MEMBERS}/{viewer.id}", json=body, headers=_headers(owner))
        assert r.status_code == 200 and r.json()["role"] == "viewer"
    assert await _actions(db) == []


async def test_an_unknown_member_is_404(client: AsyncClient, acme: Workspace, owner: User) -> None:
    for method, body in (("PATCH", {"role": "viewer"}), ("DELETE", None)):
        r = await client.request(
            method, f"{MEMBERS}/{uuid.uuid4()}", json=body, headers=_headers(owner)
        )
        assert r.status_code == 404
        assert r.json() == {"detail": "Member not found"}


# ---- Platform Admin as the safety net (decision 9)


@pytest.mark.parametrize("how", ["add", "promote"])
async def test_platform_admin_assigns_an_owner(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User, how: str
) -> None:
    admin = await make_user(db, email="pa@example.com", is_platform_admin=True)
    helper = await make_user(db, email="helper@example.com")
    if how == "add":
        r = await client.post(
            MEMBERS,
            json={"email": helper.email, "role": "owner"},
            headers=_headers(admin, mfa=True),
        )
    else:
        await make_member(db, acme.id, helper.id, Role.VIEWER)
        r = await client.patch(
            f"{MEMBERS}/{helper.id}", json={"role": "owner"}, headers=_headers(admin, mfa=True)
        )

    assert r.status_code in (200, 201), r.text
    [event] = (await db.scalars(select(AuditEvent))).all()
    assert (event.action, event.actor_type, event.workspace_id) == (
        "member.owner_assigned_by_platform",
        "platform_admin",
        acme.id,
    )


# ---- Remove and leave


async def test_an_owner_removes_a_member(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User
) -> None:
    viewer = await _member(db, acme, "viewer@acme.test", Role.VIEWER)
    r = await client.delete(f"{MEMBERS}/{viewer.id}", headers=_headers(owner))
    assert r.status_code == 204
    assert await _roles(db, acme) == {"owner@acme.test": "owner"}
    # Removed at once: their very next request no longer finds the workspace.
    after = await client.get(f"{MEMBERS}", headers=_headers(viewer))
    assert after.status_code == 404


async def test_the_last_owner_cant_be_removed(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User
) -> None:
    r = await client.delete(f"{MEMBERS}/{owner.id}", headers=_headers(owner))
    assert r.status_code == 409 and r.json() == {"detail": "last_owner"}


async def test_a_member_leaves(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User
) -> None:
    viewer = await _member(db, acme, "viewer@acme.test", Role.VIEWER)

    r = await client.delete(LEAVE, headers=_headers(viewer))

    assert r.status_code == 204
    assert await _roles(db, acme) == {"owner@acme.test": "owner"}
    [event] = (await db.scalars(select(AuditEvent))).all()
    assert (event.action, event.actor_user_id) == ("member.leave", viewer.id)


async def test_the_last_owner_cant_leave_but_one_of_two_can(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: User
) -> None:
    r = await client.delete(LEAVE, headers=_headers(owner))
    assert r.status_code == 409 and r.json() == {"detail": "last_owner"}

    await _member(db, acme, "second@acme.test", Role.OWNER)
    assert (await client.delete(LEAVE, headers=_headers(owner))).status_code == 204


async def test_leaving_a_workspace_you_are_not_in_is_404(
    client: AsyncClient, db: AsyncSession, acme: Workspace
) -> None:
    # A Platform Admin isn't a member either: there is nothing to leave.
    admin = await make_user(db, email="pa@example.com", is_platform_admin=True)
    for slug in ("acme", "no-such-workspace"):
        r = await client.delete(f"/api/v1/me/workspaces/{slug}", headers=_headers(admin, mfa=True))
        assert r.status_code == 404 and r.json() == {"detail": "Workspace not found"}


# ---- Concurrency: two owners, each taking the other's ownership at once


@pytest.fixture
def slow_after_locking(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every change pauses after reading the owners, so parallel requests
    overlap where a race would happen. Without the row locks, both would see
    two owners and both demotions would pass, leaving none."""
    original = members_crud._get_locked

    async def slow(*args: Any, **kwargs: Any) -> Any:
        found = await original(*args, **kwargs)
        await asyncio.sleep(0.05)
        return found

    monkeypatch.setattr(members_crud, "_get_locked", slow)


@pytest.mark.usefixtures("slow_after_locking")
@pytest.mark.parametrize("attempt", range(3))
async def test_two_owners_demoting_each_other_leave_one_owner(
    client: AsyncClient, db: AsyncSession, acme: Workspace, attempt: int
) -> None:
    ann = await _member(db, acme, "ann@acme.test", Role.OWNER)
    bob = await _member(db, acme, "bob@acme.test", Role.OWNER)
    demote = {"role": "viewer"}

    results = await asyncio.gather(
        client.patch(f"{MEMBERS}/{bob.id}", json=demote, headers=_headers(ann)),
        client.patch(f"{MEMBERS}/{ann.id}", json=demote, headers=_headers(bob)),
    )

    # The second request checks the caller's role after the first commits.
    assert sorted(r.status_code for r in results) == [200, 403]
    refused = next(r for r in results if r.status_code == 403)
    assert refused.json() == {"detail": "permission_denied"}
    assert list((await _roles(db, acme)).values()).count("owner") == 1


@pytest.mark.usefixtures("slow_after_locking")
async def test_two_owners_leaving_at_once_leave_one_owner(
    client: AsyncClient, db: AsyncSession, acme: Workspace
) -> None:
    ann = await _member(db, acme, "ann@acme.test", Role.OWNER)
    bob = await _member(db, acme, "bob@acme.test", Role.OWNER)

    results = await asyncio.gather(
        client.delete(LEAVE, headers=_headers(ann)), client.delete(LEAVE, headers=_headers(bob))
    )

    assert sorted(r.status_code for r in results) == [204, 409]
    assert list((await _roles(db, acme)).values()) == ["owner"]
