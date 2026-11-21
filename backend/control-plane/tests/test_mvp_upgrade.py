"""Upgrading a populated MVP database to MT.1, the way an existing install upgrades.

The MVP schema (revision db8a66008fec) gets an admin and a full config, then
`alembic upgrade head` runs on it. The admin must come out as the same person
(same id and timestamps) and Platform Admin, the config untouched, and after
startup links them to their GoTrue account they must be able to sign in.

Alembic runs its own event loop, so this test is synchronous and drives the
database through its own engine (no pooled connection crosses event loops).
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.db import get_db
from app.main import app
from app.users import crud as users_crud
from tests.fakes import FakeGoTrue, bearer, make_token

MVP_HEAD = "db8a66008fec"
CONFIG_TABLES = ("workspaces", "services", "routes", "consumers", "api_keys", "config_version")

ADMIN_ID = uuid.UUID("087ab0d5-9592-4f88-8ab0-240ead00642f")
ADMIN_EMAIL = "admin@example.com"
ADMIN_CREATED = datetime(2026, 11, 16, 15, 21, 5, tzinfo=UTC)
ADMIN_UPDATED = datetime(2026, 11, 17, 9, 0, 0, tzinfo=UTC)
WORKSPACE_ID = uuid.uuid4()
SERVICE_ID = uuid.uuid4()
CONSUMER_ID = uuid.uuid4()

_MVP_DATA = [
    (
        "INSERT INTO workspaces (id, name, slug) VALUES (:ws, 'Default', 'default')",
        {"ws": WORKSPACE_ID},
    ),
    (
        "INSERT INTO admins (id, email, password_hash, workspace_id, created_at, updated_at) "
        "VALUES (:id, :email, '$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA', :ws, :c, :u)",
        {
            "id": ADMIN_ID,
            "email": ADMIN_EMAIL,
            "ws": WORKSPACE_ID,
            "c": ADMIN_CREATED,
            "u": ADMIN_UPDATED,
        },
    ),
    (
        "INSERT INTO services (id, workspace_id, name, upstream_url, timeout_ms) "
        "VALUES (:id, :ws, 'orders', 'http://mock-backend:9000', 2000)",
        {"id": SERVICE_ID, "ws": WORKSPACE_ID},
    ),
    (
        "INSERT INTO routes (id, workspace_id, service_id, path_prefix, methods) "
        "VALUES (:id, :ws, :svc, '/v1/orders', ARRAY['GET', 'POST'])",
        {"id": uuid.uuid4(), "ws": WORKSPACE_ID, "svc": SERVICE_ID},
    ),
    (
        "INSERT INTO consumers (id, workspace_id, name, email, rate_limit_per_min) "
        "VALUES (:id, :ws, 'mobile-app', 'dev@example.com', 120)",
        {"id": CONSUMER_ID, "ws": WORKSPACE_ID},
    ),
    (
        "INSERT INTO api_keys (id, workspace_id, consumer_id, name, key_prefix, key_hash) "
        "VALUES (:id, :ws, :c, 'app', 'ak_live_abcd', :h)",
        {"id": uuid.uuid4(), "ws": WORKSPACE_ID, "c": CONSUMER_ID, "h": "a" * 64},
    ),
]


def _alembic() -> Config:
    return Config("alembic.ini")


def _engine() -> AsyncEngine:
    # The owner: at the MVP revision the API's own login has no privileges yet.
    return create_async_engine(get_settings().migration_database_url, poolclass=pool.NullPool)


async def _snapshot(engine: AsyncEngine) -> dict[str, list[dict[str, Any]]]:
    """Every row of every config table, in a stable order."""
    async with engine.connect() as conn:
        return {
            table: [
                dict(row._mapping)
                for row in await conn.execute(text(f"SELECT * FROM {table} ORDER BY 1"))
            ]
            for table in CONFIG_TABLES
        }


async def _seed_mvp() -> dict[str, list[dict[str, Any]]]:
    engine = _engine()
    async with engine.begin() as conn:
        for sql, params in _MVP_DATA:
            await conn.execute(text(sql), params)
    before = await _snapshot(engine)
    await engine.dispose()
    return before


async def _check_upgraded(before: dict[str, list[dict[str, Any]]], gotrue: FakeGoTrue) -> None:
    engine = _engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # The config is exactly as it was: no row added, changed or bumped.
        assert await _snapshot(engine) == before

        # The admin is the same person, now a Platform Admin waiting to be linked.
        async with engine.connect() as conn:
            row = (
                (await conn.execute(text("SELECT * FROM users WHERE id = :id"), {"id": ADMIN_ID}))
                .one()
                ._mapping
            )
            admins_left = await conn.scalar(text("SELECT to_regclass('admins')"))
        assert admins_left is None
        assert row["email"] == ADMIN_EMAIL
        assert (row["created_at"], row["updated_at"]) == (ADMIN_CREATED, ADMIN_UPDATED)
        assert row["is_platform_admin"] is True and row["is_active"] is True
        assert row["auth_user_id"] is None
        # ...and Owner of `default`, where all the MVP config lives (MT.2).
        async with engine.connect() as conn:
            membership = (
                await conn.execute(
                    text(
                        "SELECT workspace_id, role, added_by FROM workspace_members "
                        "WHERE user_id = :id"
                    ),
                    {"id": ADMIN_ID},
                )
            ).one()
        assert tuple(membership) == (WORKSPACE_ID, "owner", None)

        # Startup links them to their GoTrue account (here one that exists already).
        account = gotrue.add_account(ADMIN_EMAIL)
        async with sessions() as db:
            await users_crud.ensure_platform_admin(
                db, gotrue, email=ADMIN_EMAIL, password="a-long-password"
            )

        # And they sign in with GoTrue's token and see their MVP config.
        async def test_db() -> AsyncIterator[AsyncSession]:
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_db] = test_db
        headers = bearer(make_token(account, ADMIN_EMAIL))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            me = await c.get("/api/v1/me", headers=headers)
            services = await c.get("/api/v1/workspaces/default/services", headers=headers)
        assert me.status_code == 200
        assert me.json()["id"] == str(ADMIN_ID) and me.json()["is_platform_admin"] is True
        assert [s["id"] for s in services.json()["items"]] == [str(SERVICE_ID)]
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


def test_upgrade_from_a_populated_mvp_database(gotrue: FakeGoTrue) -> None:
    command.downgrade(_alembic(), MVP_HEAD)
    try:
        before = asyncio.run(_seed_mvp())
        command.upgrade(_alembic(), "head")
        asyncio.run(_check_upgraded(before, gotrue))
    finally:
        command.upgrade(_alembic(), "head")  # leave the schema as the other tests expect
