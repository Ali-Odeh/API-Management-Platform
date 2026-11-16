"""All database work for services.

Every function takes `workspace_id` and filters by it (option C), so an
admin can only ever see and change services of their own workspace. A
service in another workspace behaves exactly like one that doesn't exist.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import commit_or_conflict
from app.core.errors import NotFoundError
from app.core.pagination import PageParams
from app.services.models import UQ_SERVICE_NAME, Service
from app.services.schemas import ServiceCreate, ServiceUpdate


def _name_taken(name: str) -> dict[str, str]:
    return {UQ_SERVICE_NAME: f"A service named '{name}' already exists"}


async def list_services(
    db: AsyncSession, *, workspace_id: uuid.UUID, page: PageParams
) -> tuple[list[Service], int]:
    """One page of services (newest first) and the total count."""
    query = select(Service).where(Service.workspace_id == workspace_id)

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = await db.scalars(
        query.order_by(Service.created_at.desc(), Service.id)
        .offset(page.offset)
        .limit(page.page_size)
    )
    return list(rows), total or 0


async def get_service(
    db: AsyncSession, *, workspace_id: uuid.UUID, service_id: uuid.UUID
) -> Service:
    service = await db.scalar(
        select(Service).where(Service.id == service_id, Service.workspace_id == workspace_id)
    )
    if service is None:
        raise NotFoundError("Service not found")
    return service


async def create_service(
    db: AsyncSession, *, workspace_id: uuid.UUID, data: ServiceCreate
) -> Service:
    service = Service(workspace_id=workspace_id, **data.model_dump())
    db.add(service)
    await commit_or_conflict(db, _name_taken(data.name))
    await db.refresh(service)  # load DB-generated values (created_at, ...)
    return service


async def update_service(
    db: AsyncSession, *, workspace_id: uuid.UUID, service_id: uuid.UUID, data: ServiceUpdate
) -> Service:
    service = await get_service(db, workspace_id=workspace_id, service_id=service_id)
    changes = data.model_dump(exclude_unset=True)  # only the fields that were sent
    for field, value in changes.items():
        setattr(service, field, value)
    await commit_or_conflict(db, _name_taken(changes.get("name", service.name)))
    await db.refresh(service)  # updated_at is set by the database
    return service


async def delete_service(
    db: AsyncSession, *, workspace_id: uuid.UUID, service_id: uuid.UUID
) -> None:
    service = await get_service(db, workspace_id=workspace_id, service_id=service_id)
    await db.delete(service)
    await db.commit()
