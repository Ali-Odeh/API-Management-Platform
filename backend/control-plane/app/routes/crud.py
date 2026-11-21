"""All database work for routes, always scoped to one workspace (option C)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import crud as audit
from app.audit.changes import Changes, created, deleted, snapshot, updated
from app.audit.context import AuditContext
from app.core.db import commit_or_raise
from app.core.errors import AppError, ConflictError, NotFoundError, UnprocessableError
from app.core.pagination import PageParams, paginate
from app.routes.models import FK_ROUTE_SERVICE, UQ_ROUTE_PATH_PREFIX, Route
from app.routes.schemas import RouteCreate, RouteUpdate
from app.services import crud as services_crud
from app.workspaces.limits import check_quota

_SERVICE_NOT_FOUND = "Service not found in this workspace"


# What a route's audit events record (its whole configuration).
AUDIT_FIELDS = (
    "service_id",
    "path_prefix",
    "methods",
    "strip_prefix",
    "auth_required",
    "is_active",
)


def _errors(path_prefix: str) -> dict[str, AppError]:
    return {
        UQ_ROUTE_PATH_PREFIX: ConflictError(f"Path prefix '{path_prefix}' is already in use"),
        # Backstop for a race: the service was deleted after we checked it.
        FK_ROUTE_SERVICE: UnprocessableError(_SERVICE_NOT_FOUND),
    }


async def list_routes(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    page: PageParams,
    service_id: uuid.UUID | None = None,
) -> tuple[list[Route], int]:
    """One page of routes (newest first), optionally only those of one service.

    A filter naming a service that isn't in this workspace is bad input (422),
    as in a body, not an empty page: a reference to another workspace's object
    is always refused the same way (and an unknown id looks the same).
    """
    query = select(Route).where(Route.workspace_id == workspace_id)
    if service_id is not None:
        await _ensure_service_exists(db, workspace_id, service_id=service_id)
        query = query.where(Route.service_id == service_id)
    return await paginate(db, query.order_by(Route.created_at.desc(), Route.id), page)


async def get_route(db: AsyncSession, workspace_id: uuid.UUID, *, route_id: uuid.UUID) -> Route:
    route = await db.scalar(
        select(Route).where(Route.id == route_id, Route.workspace_id == workspace_id)
    )
    if route is None:
        raise NotFoundError("Route not found")
    return route


async def create_route(
    db: AsyncSession, workspace_id: uuid.UUID, *, data: RouteCreate, audit: AuditContext
) -> Route:
    await _ensure_service_exists(db, workspace_id=workspace_id, service_id=data.service_id)
    await check_quota(db, workspace_id, "max_routes")
    route = Route(id=uuid.uuid4(), workspace_id=workspace_id, **data.model_dump())
    db.add(route)
    _record(db, audit, route, "route.create", created(snapshot(route, AUDIT_FIELDS)))
    await commit_or_raise(db, _errors(data.path_prefix))
    await db.refresh(route)
    return route


async def update_route(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    route_id: uuid.UUID,
    data: RouteUpdate,
    audit: AuditContext,
) -> Route:
    route = await get_route(db, workspace_id=workspace_id, route_id=route_id)
    sent = data.model_dump(exclude_unset=True)
    if "service_id" in sent:
        await _ensure_service_exists(db, workspace_id=workspace_id, service_id=sent["service_id"])
    before = snapshot(route, AUDIT_FIELDS)
    for field, value in sent.items():
        setattr(route, field, value)
    if changes := updated(before, snapshot(route, AUDIT_FIELDS)):
        _record(db, audit, route, "route.update", changes)
    await commit_or_raise(db, _errors(route.path_prefix))
    await db.refresh(route)
    return route


async def delete_route(
    db: AsyncSession, workspace_id: uuid.UUID, *, route_id: uuid.UUID, audit: AuditContext
) -> None:
    route = await get_route(db, workspace_id=workspace_id, route_id=route_id)
    _record(db, audit, route, "route.delete", deleted(snapshot(route, AUDIT_FIELDS)))
    await db.delete(route)
    await db.commit()


async def _ensure_service_exists(
    db: AsyncSession, workspace_id: uuid.UUID, *, service_id: uuid.UUID
) -> None:
    """A missing service is bad input in the body (422), not a missing URL (404)."""
    try:
        await services_crud.get_service(db, workspace_id=workspace_id, service_id=service_id)
    except NotFoundError:
        raise UnprocessableError(_SERVICE_NOT_FOUND) from None


def _record(
    db: AsyncSession, audit_ctx: AuditContext, route: Route, action: str, changes: Changes
) -> None:
    audit.record(
        db,
        audit_ctx,
        workspace_id=route.workspace_id,
        action=action,
        target_type="route",
        target_id=route.id,
        target_name=route.path_prefix,
        changes=changes,
    )
