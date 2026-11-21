"""HTTP layer for routes: parse input, call crud, shape output. No SQL here."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.deps import DbSession, WorkspaceAccess, require_permission
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.core.permissions import Permission
from app.routes import crud
from app.routes.schemas import RouteCreate, RouteOut, RouteUpdate

router = APIRouter(prefix="/api/v1/workspaces/{slug}/routes", tags=["routes"])

CanRead = Annotated[WorkspaceAccess, require_permission(Permission.ROUTES_READ)]
CanWrite = Annotated[WorkspaceAccess, require_permission(Permission.ROUTES_WRITE)]

_NOT_FOUND = error_responses({404: "Route not found"})
_WRITE_ERRORS = error_responses(
    {409: "Path prefix already in use", 422: "Invalid input, or service not in this workspace"}
)


@router.get("", responses=error_responses({422: "Service not in this workspace"}))
async def list_routes(
    access: CanRead,
    db: DbSession,
    page: Pagination,
    service_id: Annotated[uuid.UUID | None, Query(description="Only this service's routes")] = None,
) -> Page[RouteOut]:
    routes, total = await crud.list_routes(
        db, workspace_id=access.workspace_id, page=page, service_id=service_id
    )
    return Page[RouteOut](items=[RouteOut.model_validate(r) for r in routes], total=total)


@router.post("", status_code=status.HTTP_201_CREATED, responses=_WRITE_ERRORS)
async def create_route(body: RouteCreate, access: CanWrite, db: DbSession) -> RouteOut:
    route = await crud.create_route(db, workspace_id=access.workspace_id, data=body)
    return RouteOut.model_validate(route)


@router.get("/{route_id}", responses=_NOT_FOUND)
async def get_route(route_id: uuid.UUID, access: CanRead, db: DbSession) -> RouteOut:
    route = await crud.get_route(db, workspace_id=access.workspace_id, route_id=route_id)
    return RouteOut.model_validate(route)


@router.patch("/{route_id}", responses=_NOT_FOUND | _WRITE_ERRORS)
async def update_route(
    route_id: uuid.UUID, body: RouteUpdate, access: CanWrite, db: DbSession
) -> RouteOut:
    route = await crud.update_route(
        db, workspace_id=access.workspace_id, route_id=route_id, data=body
    )
    return RouteOut.model_validate(route)


@router.delete("/{route_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
async def delete_route(route_id: uuid.UUID, access: CanWrite, db: DbSession) -> None:
    await crud.delete_route(db, workspace_id=access.workspace_id, route_id=route_id)
