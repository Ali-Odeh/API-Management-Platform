"""The app's startup (lifespan): the `default` workspace and the first Platform Admin."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import UpstreamError
from app.gotrue import client as gotrue_client
from app.main import app
from app.users import crud as users_crud
from app.users.models import User
from app.workspaces.models import Workspace
from tests.factories import make_user
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
    assert startup_gotrue.closed  # shutdown closes the GoTrue client


async def test_restarting_does_not_duplicate_anything(
    db: AsyncSession, startup_gotrue: FakeGoTrue
) -> None:
    for _ in range(2):
        await _run_startup()

    assert await db.scalar(select(func.count()).select_from(User)) == 1
    assert await db.scalar(select(func.count()).select_from(Workspace)) == 1
    assert len(startup_gotrue.accounts) == 1


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


async def test_the_link_follows_the_account_gotrue_holds_now(
    db: AsyncSession, gotrue: FakeGoTrue
) -> None:
    # GoTrue's database was reset: the configured email now belongs to a new
    # account. The trusted admin API decides, not a token's email claim.
    stale = await make_user(db, email="boss@example.com", is_platform_admin=True)
    account = gotrue.add_account("boss@example.com")

    user = await users_crud.ensure_platform_admin(
        db, gotrue, email="boss@example.com", password="a-long-password"
    )

    assert user.id == stale.id and user.auth_user_id == account


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
