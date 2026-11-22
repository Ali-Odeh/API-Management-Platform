"""Tests for app/core: settings, DB connection, errors and pagination."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.db import get_db, violated_constraint
from app.core.errors import ConflictError, NotFoundError, register_error_handlers
from app.core.pagination import Page, PageParams, Pagination


def test_settings_fail_fast_when_variable_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)  # ignore any .env file, use only env vars
    assert "database_url" in str(exc.value)


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+asyncpg://u:p@db:5432/x"


def test_settings_read_the_upstream_allow_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPSTREAM_ALLOW", "mock-backend, 10.20.0.0/16")
    allow = Settings(_env_file=None).allowed_upstreams
    assert allow.hosts == frozenset({"mock-backend"})
    assert [str(n) for n in allow.networks] == ["10.20.0.0/16"]


def test_settings_refuse_an_unreadable_upstream_allow_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UPSTREAM_ALLOW", "mock-backend,http://redis")
    with pytest.raises(ValidationError, match="http://redis"):
        Settings(_env_file=None)


def test_settings_read_body_sizes_with_units(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY", "2MiB")
    monkeypatch.setenv("MAX_REQUEST_BODY_CEILING", "50MB")  # decimal: 50,000,000
    settings = Settings(_env_file=None)
    assert (settings.max_request_body, settings.max_request_body_ceiling) == (
        2 * 1024**2,
        50_000_000,
    )


def test_settings_refuse_a_default_body_limit_over_the_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY", "20MiB")
    monkeypatch.setenv("MAX_REQUEST_BODY_CEILING", "10MiB")
    with pytest.raises(ValidationError, match="MAX_REQUEST_BODY must not be larger"):
        Settings(_env_file=None)


async def test_get_db_session_can_query_postgres() -> None:
    # Use the real dependency the endpoints will use, against real Postgres.
    session_gen = get_db()
    session = await anext(session_gen)
    try:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1

        # Tests must never touch the real data (see conftest.py).
        db_name = (await session.execute(text("SELECT current_database()"))).scalar_one()
        assert db_name.endswith("_test")

        version = (await session.execute(text("SHOW server_version"))).scalar_one()
        assert version.startswith("16.")
    finally:
        await session_gen.aclose()


# ---- errors.py


@pytest.mark.parametrize(
    ("error", "status"), [(NotFoundError("Thing not found"), 404), (ConflictError("Taken"), 409)]
)
async def test_app_errors_become_json_detail(error: Exception, status: int) -> None:
    mini = FastAPI()
    register_error_handlers(mini)

    @mini.get("/boom")
    async def boom() -> None:
        raise error

    async with AsyncClient(transport=ASGITransport(app=mini), base_url="http://t") as c:
        r = await c.get("/boom")
    assert r.status_code == status
    assert r.json() == {"detail": str(error)}


# ---- pagination.py


@pytest.mark.parametrize(("page", "size", "offset"), [(1, 20, 0), (2, 20, 20), (3, 5, 10)])
def test_page_offset(page: int, size: int, offset: int) -> None:
    assert PageParams(page=page, page_size=size).offset == offset


@pytest.mark.parametrize(
    ("query", "status"),
    [
        ("", 200),
        ("?page=0", 422),
        ("?page_size=0", 422),
        ("?page_size=101", 422),
        ("?page_size=100", 200),
    ],
)
async def test_pagination_query_limits(query: str, status: int) -> None:
    mini = FastAPI()

    @mini.get("/items")
    async def items(page: Pagination) -> Page[int]:
        return Page[int](items=[page.page, page.page_size], total=2)

    async with AsyncClient(transport=ASGITransport(app=mini), base_url="http://t") as c:
        r = await c.get(f"/items{query}")
    assert r.status_code == status
    if query == "":
        assert r.json() == {"items": [1, 20], "total": 2}  # the defaults


# ---- db.violated_constraint (the happy path is covered by every expect_violation)


def test_violated_constraint_is_none_without_driver_details() -> None:
    no_driver_error = IntegrityError("INSERT ...", None, None)  # type: ignore[arg-type]
    plain_cause = IntegrityError("INSERT ...", None, Exception("no constraint name"))
    assert violated_constraint(no_driver_error) is None
    assert violated_constraint(plain_cause) is None
