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
from app.core.db_roles import APP_ROLE, RETENTION_ROLE, prepare_roles
from tests.conftest import OWNER_URL

APP_URL = os.environ["DATABASE_URL"]
RETENTION_URL = os.environ["AUDIT_RETENTION_DATABASE_URL"]


async def _connect(url: str) -> asyncpg.Connection:
    return await asyncpg.connect(driver_dsn(url))


async def test_prepare_is_safe_to_repeat_and_both_logins_work() -> None:
    for _ in range(2):  # every container start runs it
        await prepare_roles(owner_url=OWNER_URL, app_url=APP_URL, retention_url=RETENTION_URL)

    for url, role in ((APP_URL, APP_ROLE), (RETENTION_URL, RETENTION_ROLE)):
        conn = await _connect(url)
        try:
            assert await conn.fetchval("SELECT current_user") == role
            rights = await conn.fetchrow(
                "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = $1", role
            )
            assert tuple(rights) == (False, False, False)
        finally:
            await conn.close()


@pytest.mark.parametrize("which", ["app", "retention"])
async def test_a_url_with_another_login_is_refused(which: str) -> None:
    # The grants are made for these exact role names; any other login in the
    # URL would run the API without them (or as the owner).
    wrong = make_url(OWNER_URL).render_as_string(hide_password=False)
    urls = {"app_url": APP_URL, "retention_url": RETENTION_URL, f"{which}_url": wrong}
    with pytest.raises(ValueError, match="must be 'apim_"):
        await prepare_roles(owner_url=OWNER_URL, **urls)


async def test_the_api_login_cannot_touch_the_migration_history() -> None:
    conn = await _connect(APP_URL)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE alembic_version SET version_num = version_num")
    finally:
        await conn.close()
