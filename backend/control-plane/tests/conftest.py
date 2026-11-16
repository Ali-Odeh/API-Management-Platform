"""Shared test setup. pytest loads this file automatically, before any test.

Tests use their own database, `apim_test`, never the real `apim`:
1. Before importing the app, point DATABASE_URL at apim_test. The app's
   engine is built from that variable, so the whole app now uses apim_test.
2. Once per test run: drop and recreate apim_test, then run the real
   Alembic migrations on it (so the migrations get tested too).
3. Before each test: empty every table, so tests can't affect each other.
"""

import asyncio
import os
from collections.abc import AsyncIterator

from sqlalchemy.engine import make_url

# ---- 1. Point the app at the test database (must happen before app imports)
_real_url = make_url(os.environ["DATABASE_URL"])
TEST_DATABASE = f"{_real_url.database}_test"
os.environ["DATABASE_URL"] = _real_url.set(database=TEST_DATABASE).render_as_string(
    hide_password=False
)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from app.auth import crud as auth_crud  # noqa: E402
from app.auth.models import Admin  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.workspaces.crud import get_or_create_default_workspace  # noqa: E402

ADMIN_EMAIL = "admin@test.local"
ADMIN_PASSWORD = "correct-horse-battery-staple"


# ---- 2. Fresh test database, once per run
async def _recreate_test_database() -> None:
    # Connect to the real DB just to run CREATE/DROP DATABASE, which can't
    # run inside a transaction, hence AUTOCOMMIT.
    admin_engine = create_async_engine(_real_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    await admin_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> None:
    asyncio.run(_recreate_test_database())
    command.upgrade(Config("alembic.ini"), "head")


# ---- 3. Clean tables before each test, close connections after
@pytest.fixture(autouse=True)
async def _clean_tables() -> AsyncIterator[None]:
    table_names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {table_names} CASCADE"))
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
async def admin(db: AsyncSession) -> Admin:
    """The default workspace plus one admin with a known password."""
    workspace = await get_or_create_default_workspace(db)
    created = await auth_crud.create_admin(
        db, email=ADMIN_EMAIL, password=ADMIN_PASSWORD, workspace_id=workspace.id
    )
    await db.commit()
    return created


@pytest.fixture
def auth_headers(admin: Admin) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(admin.id))}"}
