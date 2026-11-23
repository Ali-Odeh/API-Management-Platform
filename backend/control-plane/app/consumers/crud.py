"""All database work for consumers, always scoped to one workspace (option C)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import crud as audit
from app.audit.changes import Changes, created, deleted, snapshot, updated
from app.audit.context import AuditContext
from app.consumers.models import UQ_CONSUMER_NAME, Consumer, ConsumerStatus
from app.consumers.schemas import ConsumerCreate, ConsumerUpdate
from app.core.db import commit_or_raise
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.pagination import PageParams, paginate
from app.plans import crud as plans_crud
from app.workspaces.limits import check_quota

# What a consumer's audit events record (blocking is a `status` change).
AUDIT_FIELDS = ("name", "email", "status", "plan_id")


def _name_taken(name: str) -> dict[str, AppError]:
    return {UQ_CONSUMER_NAME: ConflictError(f"A consumer named '{name}' already exists")}


async def list_consumers(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
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
    workspace_id: uuid.UUID,
    *,
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
    db: AsyncSession, workspace_id: uuid.UUID, *, data: ConsumerCreate, audit: AuditContext
) -> Consumer:
    await check_quota(db, workspace_id, "max_consumers")
    plan = await plans_crud.plan_for_consumer(db, workspace_id, data.plan_id)
    consumer = Consumer(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        **data.model_dump(exclude={"plan_id"}),
        plan_id=plan.id,
    )
    db.add(consumer)
    _record(db, audit, consumer, "consumer.create", created(snapshot(consumer, AUDIT_FIELDS)))
    await commit_or_raise(db, _name_taken(data.name))
    await db.refresh(consumer)
    return consumer


async def update_consumer(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    consumer_id: uuid.UUID,
    data: ConsumerUpdate,
    audit: AuditContext,
) -> Consumer:
    consumer = await get_consumer(db, workspace_id=workspace_id, consumer_id=consumer_id)
    before = snapshot(consumer, AUDIT_FIELDS)
    sent = data.model_dump(exclude_unset=True)
    if "plan_id" in sent:
        sent["plan_id"] = (await plans_crud.plan_for_consumer(db, workspace_id, sent["plan_id"])).id
    for field, value in sent.items():
        setattr(consumer, field, value)
    if changes := updated(before, snapshot(consumer, AUDIT_FIELDS)):
        _record(db, audit, consumer, "consumer.update", changes)
    await commit_or_raise(db, _name_taken(consumer.name))
    await db.refresh(consumer)
    return consumer


async def delete_consumer(
    db: AsyncSession, workspace_id: uuid.UUID, *, consumer_id: uuid.UUID, audit: AuditContext
) -> None:
    """Delete a consumer. The database deletes its API keys too (ON DELETE CASCADE)."""
    consumer = await get_consumer(db, workspace_id=workspace_id, consumer_id=consumer_id)
    _record(db, audit, consumer, "consumer.delete", deleted(snapshot(consumer, AUDIT_FIELDS)))
    await db.delete(consumer)
    await db.commit()


def _record(
    db: AsyncSession, audit_ctx: AuditContext, consumer: Consumer, action: str, changes: Changes
) -> None:
    audit.record(
        db,
        audit_ctx,
        workspace_id=consumer.workspace_id,
        action=action,
        target_type="consumer",
        target_id=consumer.id,
        target_name=consumer.name,
        changes=changes,
    )
