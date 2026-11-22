"""Shared test setup. pytest loads this file automatically, before any test.

Tests use their own database, `apim_test`, never the real `apim`:
1. Before importing the app, point the database URLs at apim_test. The app's
   engine is built from DATABASE_URL, so the whole app now uses apim_test,
   with the same least-privilege login as in production (apim_app).
2. Once per test run: drop and recreate apim_test (as the owner), make sure
   the logins exist, then run the real Alembic migrations on it (so the
   migrations and their grants get tested too).
3. Before each test: empty every table (as the owner: apim_app may not
   delete audit events), so tests can't affect each other.
"""

import asyncio
import os
from collections.abc import AsyncIterator

from sqlalchemy.engine import make_url

# ---- 1. Point the app at the test database (must happen before app imports)
_real_owner_url = make_url(os.environ["MIGRATION_DATABASE_URL"])
TEST_DATABASE = f"{_real_owner_url.database}_test"
for _name in (
    "DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "AUDIT_RETENTION_DATABASE_URL",
    "PURGE_DATABASE_URL",
    "INGEST_DATABASE_URL",
):
    os.environ[_name] = (
        make_url(os.environ[_name])
        .set(database=TEST_DATABASE)
        .render_as_string(hide_password=False)
    )
OWNER_URL = os.environ["MIGRATION_DATABASE_URL"]

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, SessionLocal, driver_dsn, engine
from app.core.db_roles import prepare_roles
from app.core.permissions import Role
from app.gateway_config.models import ConfigSeq
from app.gateway_config.notifier import ConfigNotifier
from app.gateway_config.provider import ConfigProvider
from app.main import app
from app.users.models import User
from app.workspaces.crud import get_or_create_default_workspace
from app.workspaces.models import Workspace
from tests.factories import make_member, make_user, make_workspace
from tests.fakes import FakeGoTrue, bearer, make_token

ADMIN_EMAIL = "admin@test.local"


# ---- 2. Fresh test database, once per run
async def _recreate_test_database() -> None:
    # Connect to the real DB just to run CREATE/DROP DATABASE, which can't
    # run inside a transaction, hence AUTOCOMMIT.
    admin_engine = create_async_engine(_real_owner_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    await admin_engine.dispose()
    await prepare_roles(
        owner_url=OWNER_URL,
        app_url=os.environ["DATABASE_URL"],
        retention_url=os.environ["AUDIT_RETENTION_DATABASE_URL"],
        purge_url=os.environ["PURGE_DATABASE_URL"],
        ingest_url=os.environ["INGEST_DATABASE_URL"],
    )


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> None:
    asyncio.run(_recreate_test_database())
    command.upgrade(Config("alembic.ini"), "head")


# ---- 3. Clean tables before each test, close connections after
@pytest.fixture(autouse=True)
async def _clean_tables() -> AsyncIterator[None]:
    # DELETE, not TRUNCATE: our test tables hold a handful of rows, and on tiny
    # tables TRUNCATE (which takes heavy locks and rewrites files) is far slower.
    # Children before parents (reverse dependency order), so foreign keys are happy.
    # config_seq is a singleton the triggers need: reset it, never delete it
    # (the DELETEs fire the triggers and bump it, so the reset comes last).
    tables = [
        t.name for t in reversed(Base.metadata.sorted_tables) if t.name != ConfigSeq.__tablename__
    ]
    owner = create_async_engine(OWNER_URL, poolclass=pool.NullPool)
    async with owner.begin() as conn:
        for name in tables:  # asyncpg runs one statement per call
            await conn.execute(text(f"DELETE FROM {name}"))
        await conn.execute(
            text("UPDATE config_seq SET seq = 1, defaults = NULL, updated_at = now()")
        )
    await owner.dispose()
    yield
    # Each test has its own event loop, and a pooled asyncpg connection only
    # works on the loop that opened it, so close them all after each test.
    await engine.dispose()


# ---- Helpers for tests
@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An HTTP client that calls the app in memory (no real server needed)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@pytest.fixture
async def workspace(db: AsyncSession) -> Workspace:
    """The `default` workspace (the one the test admin belongs to)."""
    default = await get_or_create_default_workspace(db)
    await db.commit()
    return default


@pytest.fixture
async def other_workspace(db: AsyncSession) -> Workspace:
    """A second tenant, for isolation tests."""
    return await make_workspace(db, slug="other")


@pytest.fixture(autouse=True)
def gotrue() -> FakeGoTrue:
    """GoTrue's admin API in memory, wired in where the app looks for it."""
    fake = FakeGoTrue()
    app.state.gotrue = fake
    return fake


@pytest.fixture
async def admin(db: AsyncSession, workspace: Workspace) -> User:
    """The Platform Admin, linked to a GoTrue account and Owner of `default`
    (the MVP admin after the MT.1 and MT.2 migrations)."""
    admin = await make_user(db, email=ADMIN_EMAIL, is_platform_admin=True)
    await make_member(db, workspace.id, admin.id, Role.OWNER)  # as the MT.2 migration does
    return admin


@pytest.fixture
def auth_headers(admin: User) -> dict[str, str]:
    """The admin's GoTrue token after a password login (aal1: no MFA)."""
    assert admin.auth_user_id is not None
    return bearer(make_token(admin.auth_user_id, admin.email))


@pytest.fixture
def mfa_headers(admin: User) -> dict[str, str]:
    """The admin's GoTrue token after password and TOTP (aal2), for platform endpoints."""
    assert admin.auth_user_id is not None
    return bearer(make_token(admin.auth_user_id, admin.email, aal="aal2"))


# ---- The gateway's view: a running config provider and the internal token


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
