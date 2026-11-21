"""The app's startup (lifespan): the `default` workspace and the first Platform Admin."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.context import AuditContext
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, NotFoundError, UpstreamError
from app.core.permissions import Role
from app.gotrue import client as gotrue_client
from app.main import app
from app.members.models import WorkspaceMember
from app.users import crud as users_crud
from app.users.models import User
from app.workspaces.crud import get_or_create_default_workspace
from app.workspaces.models import Workspace
from scripts import relink_user as relink_script
from tests.factories import make_member, make_user
from tests.fakes import FakeGoTrue


@pytest.fixture
def startup_gotrue(monkeypatch: pytest.MonkeyPatch) -> FakeGoTrue:
    """The fake GoTrue the app builds at startup instead of the real client."""
    fake = FakeGoTrue()
    monkeypatch.setattr(gotrue_client, "create_gotrue_admin", lambda settings: fake)
    return fake


async def _run_startup() -> None:
    # Startup + shutdown exactly as uvicorn would.
    async with app.router.lifespan_context(app):
        pass


async def test_startup_creates_workspace_and_platform_admin(
    db: AsyncSession, startup_gotrue: FakeGoTrue
) -> None:
    settings = get_settings()
    await _run_startup()

    email = settings.platform_admin_email.lower()
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None and user.is_platform_admin and user.is_active
    # A new GoTrue account, with the password from the environment, linked at once.
    assert user.auth_user_id == startup_gotrue.accounts[email]
    assert startup_gotrue.passwords[email] == settings.platform_admin_password
    workspaces = (await db.scalars(select(Workspace))).all()
    assert [w.slug for w in workspaces] == ["default"]
    # A fresh install's `default` gets its Owner: the Platform Admin.
    owner = await db.scalar(select(WorkspaceMember))
    assert owner is not None
    assert (owner.workspace_id, owner.user_id, owner.role) == (workspaces[0].id, user.id, "owner")
    assert startup_gotrue.closed  # shutdown closes the GoTrue client


async def test_restarting_does_not_duplicate_anything(
    db: AsyncSession, startup_gotrue: FakeGoTrue
) -> None:
    for _ in range(2):
        await _run_startup()

    assert await db.scalar(select(func.count()).select_from(User)) == 1
    assert await db.scalar(select(func.count()).select_from(Workspace)) == 1
    assert await db.scalar(select(func.count()).select_from(WorkspaceMember)) == 1
    assert len(startup_gotrue.accounts) == 1


async def test_an_existing_owner_of_default_is_left_alone(
    db: AsyncSession, startup_gotrue: FakeGoTrue
) -> None:
    default = await get_or_create_default_workspace(db)
    someone = await make_user(db, email="owner@example.com")
    await make_member(db, default.id, someone.id, Role.OWNER)

    await _run_startup()

    members = (await db.scalars(select(WorkspaceMember))).all()
    assert [(m.user_id, m.role) for m in members] == [(someone.id, "owner")]


async def test_an_admin_with_a_binding_conflict_is_not_made_owner(
    db: AsyncSession, startup_gotrue: FakeGoTrue
) -> None:
    # The configured email's row is bound to another GoTrue account than the one
    # GoTrue gives that email now: no admin rights, so no ownership either.
    await make_user(db, email=get_settings().platform_admin_email.lower())
    startup_gotrue.add_account(get_settings().platform_admin_email.lower())

    await _run_startup()

    assert await db.scalar(select(func.count()).select_from(WorkspaceMember)) == 0


async def test_without_a_password_the_platform_admin_is_invited_once(
    db: AsyncSession, gotrue: FakeGoTrue
) -> None:
    for _ in range(3):  # three restarts while the invitation is still pending
        user = await users_crud.ensure_platform_admin(
            db, gotrue, email="Boss@Example.COM", password=""
        )

    assert gotrue.invited == ["boss@example.com"]  # never sent again
    assert user.email == "boss@example.com"
    assert user.auth_user_id == gotrue.accounts["boss@example.com"]


async def test_the_migrated_admin_is_linked_through_the_admin_api(
    db: AsyncSession, gotrue: FakeGoTrue
) -> None:
    # The MVP admin, moved to `users` by the migration with no GoTrue id, and a
    # GoTrue account that already exists for that email (e.g. made by hand).
    migrated = await make_user(db, email="boss@example.com", linked=False, is_platform_admin=True)
    account = gotrue.add_account("boss@example.com")

    user = await users_crud.ensure_platform_admin(
        db, gotrue, email="boss@example.com", password="a-long-password"
    )

    assert user.id == migrated.id
    assert user.auth_user_id == account
    assert "boss@example.com" not in gotrue.passwords  # the password was not reset


async def test_a_bound_admin_stays_bound_when_gotrue_disagrees(
    db: AsyncSession, gotrue: FakeGoTrue, caplog: pytest.LogCaptureFixture
) -> None:
    # GoTrue now gives the configured email another account (e.g. its database
    # was reset). A matching email proves nothing: keep the binding, report it.
    admin = await make_user(db, email="boss@example.com", is_platform_admin=True)
    bound = admin.auth_user_id
    other = gotrue.add_account("boss@example.com")

    for _ in range(2):  # every restart reports it, none rebinds
        user = await users_crud.ensure_platform_admin(
            db, gotrue, email="boss@example.com", password="a-long-password"
        )

    assert user.id == admin.id and user.auth_user_id == bound
    assert "boss@example.com" not in gotrue.passwords  # nothing created either
    conflicts = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(conflicts) == 2
    assert str(bound) in conflicts[0].getMessage()
    assert str(other) in conflicts[0].getMessage()
    assert "scripts.relink_user" in conflicts[0].getMessage()


async def test_a_bound_admin_whose_account_is_gone_is_reported_not_recreated(
    db: AsyncSession, gotrue: FakeGoTrue, caplog: pytest.LogCaptureFixture
) -> None:
    admin = await make_user(db, email="boss@example.com", is_platform_admin=True)

    user = await users_crud.ensure_platform_admin(
        db, gotrue, email="boss@example.com", password="a-long-password"
    )

    assert user.auth_user_id == admin.auth_user_id
    assert gotrue.accounts == {} and gotrue.invited == []
    assert "no account" in caplog.records[-1].getMessage()


async def test_a_bound_admin_that_matches_is_quiet(
    db: AsyncSession, gotrue: FakeGoTrue, caplog: pytest.LogCaptureFixture
) -> None:
    admin = await make_user(db, email="boss@example.com", is_platform_admin=True)
    assert admin.auth_user_id is not None
    gotrue.accounts["boss@example.com"] = admin.auth_user_id

    await users_crud.ensure_platform_admin(
        db, gotrue, email="boss@example.com", password="a-long-password"
    )

    assert not [r for r in caplog.records if r.levelname == "ERROR"]


@pytest.mark.parametrize("replacement_exists", [True, False], ids=["mismatch", "missing"])
async def test_unverified_binding_cannot_promote_an_existing_user(
    db: AsyncSession, gotrue: FakeGoTrue, replacement_exists: bool
) -> None:
    person = await make_user(db, email="boss@example.com")
    bound = person.auth_user_id
    if replacement_exists:
        gotrue.add_account(person.email)

    await users_crud.ensure_platform_admin(
        db, gotrue, email=person.email, password="a-long-password"
    )

    await db.refresh(person)
    assert person.auth_user_id == bound
    assert person.is_platform_admin is False


# ---- Explicit recovery: relink_user (scripts/relink_user.py)


@pytest.mark.parametrize("is_active", [True, False], ids=["active", "disabled"])
async def test_relink_binds_the_account_gotrue_holds_now(
    db: AsyncSession, gotrue: FakeGoTrue, is_active: bool
) -> None:
    admin = await make_user(
        db, email="boss@example.com", is_platform_admin=True, is_active=is_active
    )
    previous = admin.auth_user_id
    new = gotrue.add_account("boss@example.com")

    old, bound = await users_crud.relink_user(
        db, gotrue, email="Boss@Example.com", audit=AuditContext.system()
    )

    assert (old, bound) == (previous, new)
    await db.refresh(admin)
    assert admin.auth_user_id == new and admin.is_platform_admin
    assert admin.is_active is is_active
    assert gotrue.ban_calls == ([] if is_active else [(new, True)])
    assert (new in gotrue.banned) is (not is_active)


@pytest.mark.parametrize("account_missing", [True, False], ids=["missing", "timeout"])
async def test_relink_keeps_the_old_binding_if_gotrue_cannot_confirm_the_ban(
    db: AsyncSession, gotrue: FakeGoTrue, account_missing: bool
) -> None:
    person = await make_user(db, email="boss@example.com", is_active=False)
    previous = person.auth_user_id
    new = gotrue.add_account(person.email)
    gotrue.account_missing_on_ban = account_missing
    gotrue.fail_after_ban = not account_missing

    with pytest.raises(NotFoundError if account_missing else UpstreamError):
        await users_crud.relink_user(db, gotrue, email=person.email, audit=AuditContext.system())

    assert person.auth_user_id == previous
    await db.rollback()
    await db.refresh(person)
    assert person.auth_user_id == previous and not person.is_active
    assert gotrue.ban_calls == [(new, True)]


async def test_relink_unknown_user_or_account_changes_nothing(
    db: AsyncSession, gotrue: FakeGoTrue
) -> None:
    with pytest.raises(NotFoundError, match="User not found"):
        await users_crud.relink_user(
            db, gotrue, email="nobody@example.com", audit=AuditContext.system()
        )

    admin = await make_user(db, email="boss@example.com")
    with pytest.raises(NotFoundError, match="no account"):
        await users_crud.relink_user(
            db, gotrue, email="boss@example.com", audit=AuditContext.system()
        )
    await db.refresh(admin)
    assert admin.auth_user_id is not None


async def test_relink_never_takes_another_persons_account(
    db: AsyncSession, gotrue: FakeGoTrue
) -> None:
    other = await make_user(db, email="other@example.com")
    assert other.auth_user_id is not None
    await make_user(db, email="boss@example.com")
    gotrue.accounts["boss@example.com"] = other.auth_user_id  # a broken GoTrue answer

    with pytest.raises(ConflictError, match="other@example.com"):
        await users_crud.relink_user(
            db, gotrue, email="boss@example.com", audit=AuditContext.system()
        )


async def test_relink_script(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGoTrue()
    monkeypatch.setattr(gotrue_client, "create_gotrue_admin", lambda settings: fake)
    admin = await make_user(db, email="boss@example.com")
    previous = admin.auth_user_id
    new = fake.add_account("boss@example.com")

    assert await relink_script.relink("boss@example.com") == 0
    assert await relink_script.relink("nobody@example.com") == 1

    out = capsys.readouterr()
    assert f"{previous} -> {new}" in out.out
    assert "Not changed: User not found" in out.err
    assert fake.closed


def test_relink_script_needs_one_email(capsys: pytest.CaptureFixture[str]) -> None:
    assert relink_script.main([]) == 2
    assert "relink_user" in capsys.readouterr().err


async def test_an_existing_person_is_made_platform_admin(
    db: AsyncSession, gotrue: FakeGoTrue
) -> None:
    person = await make_user(db, email="boss@example.com")
    assert person.auth_user_id is not None
    gotrue.accounts["boss@example.com"] = person.auth_user_id

    user = await users_crud.ensure_platform_admin(
        db, gotrue, email="boss@example.com", password="a-long-password"
    )

    assert user.id == person.id and user.is_platform_admin
    assert user.auth_user_id == person.auth_user_id


class _RacingGoTrue(FakeGoTrue):
    """The account appears between our lookup and our create (another process)."""

    async def create_user(self, email: str, password: str) -> uuid.UUID | None:
        self.add_account(email)
        return None


async def test_an_account_made_meanwhile_is_found_again(db: AsyncSession) -> None:
    gotrue = _RacingGoTrue()
    user = await users_crud.ensure_platform_admin(
        db, gotrue, email="boss@example.com", password="a-long-password"
    )
    assert user.auth_user_id == gotrue.accounts["boss@example.com"]


async def test_no_account_at_all_after_create_is_an_error(db: AsyncSession) -> None:
    class Broken(FakeGoTrue):
        async def create_user(self, email: str, password: str) -> uuid.UUID | None:
            return None  # "already exists", yet the lookup finds nothing

    with pytest.raises(UpstreamError):
        await users_crud.ensure_platform_admin(
            db, Broken(), email="boss@example.com", password="a-long-password"
        )


def test_settings_require_a_long_gotrue_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOTRUE_JWT_SECRET", "too-short")
    with pytest.raises(ValueError, match="gotrue_jwt_secret"):
        Settings(_env_file=None)
