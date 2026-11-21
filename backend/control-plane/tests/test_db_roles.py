"""The database logins (app/core/db_roles.py) and what the API's own login may do.

Careful: roles belong to the whole Postgres server, which the test database
shares with the dev database. These tests only ever set the passwords that
.env already holds.
"""

import os

import asyncpg
import pytest
from sqlalchemy import make_url

from app.core.db import driver_dsn
from app.core.db_roles import APP_ROLE, PURGE_ROLE, RETENTION_ROLE, prepare_roles
from tests.conftest import OWNER_URL

APP_URL = os.environ["DATABASE_URL"]
RETENTION_URL = os.environ["AUDIT_RETENTION_DATABASE_URL"]
PURGE_URL = os.environ["PURGE_DATABASE_URL"]
URLS = {"app_url": APP_URL, "retention_url": RETENTION_URL, "purge_url": PURGE_URL}


async def _connect(url: str) -> asyncpg.Connection:
    return await asyncpg.connect(driver_dsn(url))


async def test_prepare_is_safe_to_repeat_and_every_login_works() -> None:
    for _ in range(2):  # every container start runs it
        await prepare_roles(owner_url=OWNER_URL, **URLS)

    for url, role in (
        (APP_URL, APP_ROLE),
        (RETENTION_URL, RETENTION_ROLE),
        (PURGE_URL, PURGE_ROLE),
    ):
        conn = await _connect(url)
        try:
            assert await conn.fetchval("SELECT current_user") == role
            rights = await conn.fetchrow(
                "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = $1", role
            )
            assert tuple(rights) == (False, False, False)
        finally:
            await conn.close()


@pytest.mark.parametrize("which", ["app", "retention", "purge"])
async def test_a_url_with_another_login_is_refused(which: str) -> None:
    # The grants are made for these exact role names; any other login in the
    # URL would run the API without them (or as the owner).
    wrong = make_url(OWNER_URL).render_as_string(hide_password=False)
    urls = {**URLS, f"{which}_url": wrong}
    with pytest.raises(ValueError, match="must be 'apim_"):
        await prepare_roles(owner_url=OWNER_URL, **urls)


async def test_the_api_login_cannot_touch_the_migration_history() -> None:
    conn = await _connect(APP_URL)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE alembic_version SET version_num = version_num")
    finally:
        await conn.close()


async def test_the_purge_login_cannot_write_config() -> None:
    # It deletes a purged workspace's data; it never adds or changes config.
    conn = await _connect(PURGE_URL)
    try:
        for statement in (
            "UPDATE services SET timeout_ms = timeout_ms",
            "UPDATE workspaces SET name = name",
            "INSERT INTO workspaces (id, name, slug) VALUES (gen_random_uuid(), 'x', 'xyz')",
            "UPDATE audit_events SET action = action",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(statement)
    finally:
        await conn.close()
