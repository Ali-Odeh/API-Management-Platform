"""`updated_at` is kept by the database (the `set_updated_at` trigger), not only by the ORM."""

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Base
from app.workspaces.models import Workspace
from tests.factories import make_service

TABLES_WITH_UPDATED_AT = sorted(t.name for t in Base.metadata.sorted_tables if "updated_at" in t.c)


def test_discovery_finds_the_tables() -> None:
    # If discovery broke, the parametrized test below would silently test nothing.
    assert {"workspaces", "services", "routes", "consumers", "api_keys"} <= set(
        TABLES_WITH_UPDATED_AT
    )


@pytest.mark.parametrize("table", TABLES_WITH_UPDATED_AT)
async def test_every_table_has_the_trigger(db: AsyncSession, table: str) -> None:
    # A new table without the trigger would quietly fall back to stale timestamps.
    found = await db.scalar(
        text(
            "SELECT count(*) FROM pg_trigger "
            "WHERE tgrelid = CAST(:table AS regclass) AND tgname = :name"
        ),
        {"table": table, "name": f"trg_{table}_set_updated_at"},
    )
    assert found == 1


async def _updated_at(db: AsyncSession, service_id: object) -> datetime:
    value = await db.scalar(
        text("SELECT updated_at FROM services WHERE id = :id"), {"id": service_id}
    )
    assert isinstance(value, datetime)
    return value


async def test_plain_sql_update_moves_updated_at(db: AsyncSession, workspace: Workspace) -> None:
    service = await make_service(db, workspace.id)
    before = await _updated_at(db, service.id)

    # A bulk statement the ORM never sees; now() is the transaction's start time,
    # so this later transaction always gets a later value.
    await db.execute(
        text("UPDATE services SET timeout_ms = 1234 WHERE id = :id"), {"id": service.id}
    )
    await db.commit()

    assert await _updated_at(db, service.id) > before


async def test_update_with_the_same_values_keeps_updated_at(
    db: AsyncSession, workspace: Workspace
) -> None:
    service = await make_service(db, workspace.id, timeout_ms=5000)
    before = await _updated_at(db, service.id)

    await db.execute(
        text("UPDATE services SET timeout_ms = 5000 WHERE id = :id"), {"id": service.id}
    )
    await db.commit()

    assert await _updated_at(db, service.id) == before
