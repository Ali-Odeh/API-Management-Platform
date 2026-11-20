"""The app's startup (lifespan): the `default` workspace and the first Platform Admin."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
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


async def test_without_a_password_the_platform_admin_is_invited(
    db: AsyncSession, gotrue: FakeGoTrue
) -> None:
    user = await users_crud.ensure_platform_admin(db, gotrue, email="Boss@Example.COM", password="")

    assert gotrue.invited == ["boss@example.com"]
    assert user.email == "boss@example.com"
    assert user.auth_user_id == gotrue.accounts["boss@example.com"]


async def test_existing_gotrue_account_is_linked_on_first_login(
    db: AsyncSession, gotrue: FakeGoTrue
) -> None:
    # The account exists in GoTrue already (e.g. the database was reset):
    # GoTrue doesn't tell us its id, so the first request with its token links it.
    gotrue.add_account("boss@example.com")
    user = await users_crud.ensure_platform_admin(
        db, gotrue, email="boss@example.com", password="a-long-password"
    )

    assert user.is_platform_admin and user.auth_user_id is None
    assert "boss@example.com" not in gotrue.passwords  # the password was not reset


async def test_an_existing_person_is_made_platform_admin(
    db: AsyncSession, gotrue: FakeGoTrue
) -> None:
    person = await make_user(db, email="boss@example.com")
    gotrue.accounts["boss@example.com"] = person.auth_user_id  # type: ignore[assignment]

    user = await users_crud.ensure_platform_admin(
        db, gotrue, email="boss@example.com", password="a-long-password"
    )

    assert user.id == person.id and user.is_platform_admin
    assert user.auth_user_id == person.auth_user_id


def test_settings_require_a_long_gotrue_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOTRUE_JWT_SECRET", "too-short")
    with pytest.raises(ValueError, match="gotrue_jwt_secret"):
        Settings(_env_file=None)
