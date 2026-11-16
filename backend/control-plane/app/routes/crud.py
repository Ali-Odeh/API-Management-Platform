"""All database work for routes, always scoped to one workspace (option C)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import commit_or_raise
from app.core.errors import AppError, ConflictError, NotFoundError, UnprocessableError
from app.core.pagination import PageParams, paginate
from app.routes.models import FK_ROUTE_SERVICE, UQ_ROUTE_PATH_PREFIX, Route
from app.routes.schemas import RouteCreate, RouteUpdate
from app.services import crud as services_crud

_SERVICE_NOT_FOUND = "Service not found in this workspace"


def _errors(path_prefix: str) -> dict[str, AppError]:
    return {
        UQ_ROUTE_PATH_PREFIX: ConflictError(f"Path prefix '{path_prefix}' is already in use"),
        # Backstop for a race: the service was deleted after we checked it.
        FK_ROUTE_SERVICE: UnprocessableError(_SERVICE_NOT_FOUND),
    }


async def list_routes(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    page: PageParams,
    service_id: uuid.UUID | None = None,
) -> tuple[list[Route], int]:
    """One page of routes (newest first), optionally only those of one service."""
    query = select(Route).where(Route.workspace_id == workspace_id)
    if service_id is not None:
        query = query.where(Route.service_id == service_id)
    return await paginate(db, query.order_by(Route.created_at.desc(), Route.id), page)


async def get_route(db: AsyncSession, *, workspace_id: uuid.UUID, route_id: uuid.UUID) -> Route:
    route = await db.scalar(
        select(Route).where(Route.id == route_id, Route.workspace_id == workspace_id)
    )
    if route is None:
        raise NotFoundError("Route not found")
    return route


async def create_route(db: AsyncSession, *, workspace_id: uuid.UUID, data: RouteCreate) -> Route:
    await _ensure_service_exists(db, workspace_id=workspace_id, service_id=data.service_id)
    route = Route(workspace_id=workspace_id, **data.model_dump())
    db.add(route)
    await commit_or_raise(db, _errors(data.path_prefix))
    await db.refresh(route)
    return route


async def update_route(
    db: AsyncSession, *, workspace_id: uuid.UUID, route_id: uuid.UUID, data: RouteUpdate
) -> Route:
    route = await get_route(db, workspace_id=workspace_id, route_id=route_id)
    changes = data.model_dump(exclude_unset=True)
    if "service_id" in changes:
        await _ensure_service_exists(
            db, workspace_id=workspace_id, service_id=changes["service_id"]
        )
    for field, value in changes.items():
        setattr(route, field, value)
    await commit_or_raise(db, _errors(route.path_prefix))
    await db.refresh(route)
    return route


async def delete_route(db: AsyncSession, *, workspace_id: uuid.UUID, route_id: uuid.UUID) -> None:
    route = await get_route(db, workspace_id=workspace_id, route_id=route_id)
    await db.delete(route)
    await db.commit()


async def _ensure_service_exists(
    db: AsyncSession, *, workspace_id: uuid.UUID, service_id: uuid.UUID
) -> None:
    """A missing service is bad input in the body (422), not a missing URL (404)."""
    try:
        await services_crud.get_service(db, workspace_id=workspace_id, service_id=service_id)
    except NotFoundError:
        raise UnprocessableError(_SERVICE_NOT_FOUND) from None
