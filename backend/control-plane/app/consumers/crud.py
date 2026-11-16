"""All database work for consumers, always scoped to one workspace (option C)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.consumers.models import UQ_CONSUMER_NAME, Consumer, ConsumerStatus
from app.consumers.schemas import ConsumerCreate, ConsumerUpdate
from app.core.db import commit_or_raise
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.pagination import PageParams, paginate


def _name_taken(name: str) -> dict[str, AppError]:
    return {UQ_CONSUMER_NAME: ConflictError(f"A consumer named '{name}' already exists")}


async def list_consumers(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    page: PageParams,
    status: ConsumerStatus | None = None,
) -> tuple[list[Consumer], int]:
    """One page of consumers (newest first), optionally only active or only blocked."""
    query = select(Consumer).where(Consumer.workspace_id == workspace_id)
    if status is not None:
        query = query.where(Consumer.status == status)
    return await paginate(db, query.order_by(Consumer.created_at.desc(), Consumer.id), page)


async def get_consumer(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    consumer_id: uuid.UUID,
    for_update: bool = False,
) -> Consumer:
    """`for_update=True` locks the row until commit (SELECT ... FOR UPDATE), so
    parallel requests touching the same consumer wait for each other."""
    query = select(Consumer).where(
        Consumer.id == consumer_id, Consumer.workspace_id == workspace_id
    )
    consumer = await db.scalar(query.with_for_update() if for_update else query)
    if consumer is None:
        raise NotFoundError("Consumer not found")
    return consumer


async def create_consumer(
    db: AsyncSession, *, workspace_id: uuid.UUID, data: ConsumerCreate
) -> Consumer:
    consumer = Consumer(workspace_id=workspace_id, **data.model_dump())
    db.add(consumer)
    await commit_or_raise(db, _name_taken(data.name))
    await db.refresh(consumer)
    return consumer


async def update_consumer(
    db: AsyncSession, *, workspace_id: uuid.UUID, consumer_id: uuid.UUID, data: ConsumerUpdate
) -> Consumer:
    consumer = await get_consumer(db, workspace_id=workspace_id, consumer_id=consumer_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(consumer, field, value)
    await commit_or_raise(db, _name_taken(consumer.name))
    await db.refresh(consumer)
    return consumer


async def delete_consumer(
    db: AsyncSession, *, workspace_id: uuid.UUID, consumer_id: uuid.UUID
) -> None:
    """Delete a consumer. The database deletes its API keys too (ON DELETE CASCADE)."""
    consumer = await get_consumer(db, workspace_id=workspace_id, consumer_id=consumer_id)
    await db.delete(consumer)
    await db.commit()
