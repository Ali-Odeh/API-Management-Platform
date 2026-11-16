"""Shared test fixtures. pytest loads this file automatically."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.db import engine
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An HTTP client that calls the app in memory (no real server needed)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
async def _dispose_engine() -> AsyncIterator[None]:
    """Close pooled DB connections after each test.

    Each test runs on its own event loop, and an asyncpg connection only
    works on the loop that opened it. Disposing the pool after each test
    keeps the next test from reusing a connection from a dead loop.
    """
    yield
    await engine.dispose()
