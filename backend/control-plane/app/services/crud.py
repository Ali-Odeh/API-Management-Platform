"""All database work for services.

Every function takes `workspace_id` and filters by it (option C), so an
admin can only ever see and change services of their own workspace. A
service in another workspace behaves exactly like one that doesn't exist.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import commit_or_raise
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.pagination import PageParams, paginate
from app.routes.models import FK_ROUTE_SERVICE
from app.services.models import UQ_SERVICE_NAME, Service
from app.services.schemas import ServiceCreate, ServiceUpdate


def _name_taken(name: str) -> dict[str, AppError]:
    return {UQ_SERVICE_NAME: ConflictError(f"A service named '{name}' already exists")}


async def list_services(
    db: AsyncSession, *, workspace_id: uuid.UUID, page: PageParams
) -> tuple[list[Service], int]:
    """One page of services (newest first) and the total count."""
    query = (
        select(Service)
        .where(Service.workspace_id == workspace_id)
        .order_by(Service.created_at.desc(), Service.id)
    )
    return await paginate(db, query, page)


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
    await commit_or_raise(db, _name_taken(data.name))
    await db.refresh(service)  # load DB-generated values (created_at, ...)
    return service


async def update_service(
    db: AsyncSession, *, workspace_id: uuid.UUID, service_id: uuid.UUID, data: ServiceUpdate
) -> Service:
    service = await get_service(db, workspace_id=workspace_id, service_id=service_id)
    changes = data.model_dump(exclude_unset=True)  # only the fields that were sent
    for field, value in changes.items():
        setattr(service, field, value)
    await commit_or_raise(db, _name_taken(changes.get("name", service.name)))
    await db.refresh(service)  # updated_at is set by the database
    return service


async def delete_service(
    db: AsyncSession, *, workspace_id: uuid.UUID, service_id: uuid.UUID
) -> None:
    """Delete a service. Refused (409) while routes still point to it."""
    service = await get_service(db, workspace_id=workspace_id, service_id=service_id)
    await db.delete(service)
    await commit_or_raise(
        db, {FK_ROUTE_SERVICE: ConflictError("Service still has routes. Delete its routes first.")}
    )
