"""Upgrading a populated MVP database to MT.1, the way an existing install upgrades.

The MVP schema (revision db8a66008fec) gets an admin and a full config, then
`alembic upgrade head` runs on it. The admin must come out as the same person
(same id and timestamps) and Platform Admin, the config untouched, and after
startup links them to their GoTrue account they must be able to sign in.

Alembic runs its own event loop, so this test is synchronous and drives the
database through its own engine (no pooled connection crosses event loops).
"""

import asyncio
import os
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

from app.core.db import get_db
from app.main import app
from app.users import crud as users_crud
from tests.fakes import FakeGoTrue, bearer, make_token

MVP_HEAD = "db8a66008fec"
CONFIG_TABLES = ("workspaces", "services", "routes", "consumers", "api_keys")
# Workspace columns added after the MVP that are NULL on an upgraded row (MT.5).
NEW_WORKSPACE_COLUMNS = (
    "status_reason", "deleted_at", "purge_after",
    "rate_limit_per_min", "max_concurrent", "max_services", "max_routes", "max_consumers",
)  # fmt: skip

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
    return create_async_engine(os.environ["MIGRATION_DATABASE_URL"], poolclass=pool.NullPool)


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


def _columns_of(
    snapshot: dict[str, list[dict[str, Any]]], like: dict[str, list[dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    """`snapshot` with only the columns each table of `like` has (the MVP's)."""
    return {
        table: [{c: row[c] for c in like[table][0]} if like[table] else row for row in rows]
        for table, rows in snapshot.items()
    }


async def _seed_mvp() -> dict[str, list[dict[str, Any]]]:
    engine = _engine()
    async with engine.begin() as conn:
        for sql, params in _MVP_DATA:
            await conn.execute(text(sql), params)
    before = await _snapshot(engine)
    async with engine.connect() as conn:  # the MVP's single config version
        version = await conn.scalar(text("SELECT version FROM config_version"))
    before["config_version"] = [{"version": version}]
    await engine.dispose()
    return before


async def _check_upgraded(before: dict[str, list[dict[str, Any]]], gotrue: FakeGoTrue) -> None:
    engine = _engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # The config is exactly as it was: no row added, changed or bumped.
        after = await _snapshot(engine)
        mvp_version = before.pop("config_version")[0]["version"]
        # MT.10 moved each service's upstream_url into its one target, with the
        # service's id (the target id the gateway already used), weight 100.
        upstream_urls = {row["id"]: row.pop("upstream_url") for row in before["services"]}
        # V2.2 moved each consumer's own limit onto a plan of its workspace.
        limits = {row["id"]: row.pop("rate_limit_per_min") for row in before["consumers"]}
        assert _columns_of(after, before) == before
        async with engine.connect() as conn:
            plans = (
                await conn.execute(
                    text(
                        "SELECT c.id, p.name, p.rate_limit_per_min, p.monthly_quota "
                        "FROM consumers c JOIN plans p ON p.id = c.plan_id"
                    )
                )
            ).all()
            defaults = await conn.scalar(text("SELECT count(*) FROM plans WHERE is_default"))
        # No consumer's limit changed: 120/min is its own legacy plan, no quota.
        assert {(p.id, p.name, p.rate_limit_per_min, p.monthly_quota) for p in plans} == {
            (CONSUMER_ID, f"Legacy {limits[CONSUMER_ID]}/min", limits[CONSUMER_ID], None)
        }
        assert defaults == 1  # `default` got its default plan
        async with engine.connect() as conn:
            targets = (await conn.execute(text("SELECT * FROM service_targets"))).all()
        assert [(t.id, t.service_id, t.url, t.weight, t.is_active) for t in targets] == [
            (SERVICE_ID, SERVICE_ID, upstream_urls[SERVICE_ID], 100, True)
        ]
        # The sequence continues from the MVP's version (MT.6), so a running
        # gateway isn't sent back to the start; the workspace starts at version 1.
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT seq FROM config_seq")) == mvp_version
            config = (await conn.execute(text("SELECT * FROM workspace_config"))).one()._mapping
        assert (config["workspace_id"], config["version"], config["seq"]) == (
            WORKSPACE_ID, 1, mvp_version,
        )  # fmt: skip
        # Columns added since the MVP start at their defaults (MT.5: active,
        # the platform's limits).
        [workspace] = after["workspaces"]
        assert workspace["status"] == "active"
        assert set(workspace.items()) - set(before["workspaces"][0].items()) == {
            ("status", "active"),
            *((column, None) for column in NEW_WORKSPACE_COLUMNS),
        }

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
