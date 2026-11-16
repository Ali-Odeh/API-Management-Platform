"""Consumers: database rules. (The API arrives in step 2.2.)"""

from sqlalchemy import TextClause, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.consumers.models import Consumer, ConsumerStatus
from app.keys.models import ApiKey
from app.workspaces.models import Workspace
from tests.factories import expect_violation, make_api_key, make_consumer


async def test_defaults(db: AsyncSession, workspace: Workspace) -> None:
    consumer = await make_consumer(db, workspace.id)
    await db.refresh(consumer)
    assert consumer.status is ConsumerStatus.ACTIVE  # a real enum, not just a string
    assert consumer.rate_limit_per_min == 60  # plan default


async def test_name_unique_per_workspace(
    db: AsyncSession, workspace: Workspace, other_workspace: Workspace
) -> None:
    await make_consumer(db, workspace.id, name="mobile-app")
    await make_consumer(db, other_workspace.id, name="mobile-app")  # other tenant: fine
    await expect_violation(
        db, "uq_consumers_workspace_id_name", make_consumer(db, workspace.id, name="mobile-app")
    )


async def test_email_may_repeat(db: AsyncSession, workspace: Workspace) -> None:
    # One developer can own several apps.
    await make_consumer(db, workspace.id, name="app-1", email="dev@example.com")
    await make_consumer(db, workspace.id, name="app-2", email="dev@example.com")


async def test_unknown_status_rejected(db: AsyncSession, workspace: Workspace) -> None:
    consumer = await make_consumer(db, workspace.id)
    # Raw SQL bypasses Python's enum, so this really tests the database CHECK.
    statement = text("UPDATE consumers SET status = 'deleted' WHERE id = :id")
    await expect_violation(
        db, "ck_consumers_status", _execute_and_commit(db, statement, {"id": consumer.id})
    )


async def test_rate_limit_must_be_positive(db: AsyncSession, workspace: Workspace) -> None:
    await expect_violation(
        db,
        "ck_consumers_rate_limit_positive",
        make_consumer(db, workspace.id, rate_limit_per_min=0),
    )


async def test_deleting_consumer_deletes_its_keys(db: AsyncSession, workspace: Workspace) -> None:
    consumer = await make_consumer(db, workspace.id)
    await make_api_key(db, workspace.id, consumer.id)
    await make_api_key(db, workspace.id, consumer.id)

    await db.execute(text("DELETE FROM consumers WHERE id = :id"), {"id": consumer.id})
    await db.commit()

    # Count with real queries: db.get() would answer from the session's
    # in-memory cache, which doesn't know about the raw DELETE above.
    assert await db.scalar(select(func.count()).select_from(Consumer)) == 0
    assert await db.scalar(select(func.count()).select_from(ApiKey)) == 0


async def _execute_and_commit(
    db: AsyncSession, statement: TextClause, params: dict[str, object]
) -> None:
    await db.execute(statement, params)
    await db.commit()
