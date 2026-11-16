"""Tests for app/core: settings and the database connection."""

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.core.config import Settings
from app.core.db import get_db


def test_settings_fail_fast_when_variable_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)  # ignore any .env file, use only env vars
    assert "database_url" in str(exc.value)


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+asyncpg://u:p@db:5432/x"


async def test_get_db_session_can_query_postgres() -> None:
    # Use the real dependency the endpoints will use, against the real DB.
    session_gen = get_db()
    session = await anext(session_gen)
    try:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1

        version = (await session.execute(text("SHOW server_version"))).scalar_one()
        assert version.startswith("16.")
    finally:
        await session_gen.aclose()
