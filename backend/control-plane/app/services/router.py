"""HTTP layer for services and their targets: parse input, call crud, shape output. No SQL here."""

import uuid
from typing import Annotated

from fastapi import APIRouter, status

from app.core.deps import DbSession, WorkspaceAccess, require_permission
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.core.permissions import Permission
from app.services import crud
from app.services.schemas import (
    ServiceCreate,
    ServiceOut,
    ServiceUpdate,
    TargetCreate,
    TargetOut,
    TargetUpdate,
)

router = APIRouter(prefix="/api/v1/workspaces/{slug}/services", tags=["services"])

CanRead = Annotated[WorkspaceAccess, require_permission(Permission.SERVICES_READ)]
CanWrite = Annotated[WorkspaceAccess, require_permission(Permission.SERVICES_WRITE)]

_NOT_FOUND = error_responses({404: "Service not found"})
_CONFLICT = error_responses({409: "Name already used in this workspace"})
_CREATE_CONFLICT = error_responses(
    {409: "Name already used in this workspace, or quota_exceeded (the workspace's quota)"}
)
_HAS_ROUTES = error_responses({409: "Service still has routes"})


@router.get("")
async def list_services(access: CanRead, db: DbSession, page: Pagination) -> Page[ServiceOut]:
    services, total = await crud.list_services(db, workspace_id=access.workspace_id, page=page)
    return Page[ServiceOut](items=[ServiceOut.model_validate(s) for s in services], total=total)


@router.post("", status_code=status.HTTP_201_CREATED, responses=_CREATE_CONFLICT)
async def create_service(body: ServiceCreate, access: CanWrite, db: DbSession) -> ServiceOut:
    service = await crud.create_service(
        db, workspace_id=access.workspace_id, audit=access.audit, data=body
    )
    return ServiceOut.model_validate(service)


@router.get("/{service_id}", responses=_NOT_FOUND)
async def get_service(service_id: uuid.UUID, access: CanRead, db: DbSession) -> ServiceOut:
    service = await crud.get_service(db, workspace_id=access.workspace_id, service_id=service_id)
    return ServiceOut.model_validate(service)


@router.patch("/{service_id}", responses=_NOT_FOUND | _CONFLICT)
async def update_service(
    service_id: uuid.UUID, body: ServiceUpdate, access: CanWrite, db: DbSession
) -> ServiceOut:
    service = await crud.update_service(
        db, workspace_id=access.workspace_id, audit=access.audit, service_id=service_id, data=body
    )
    return ServiceOut.model_validate(service)


@router.delete(
    "/{service_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND | _HAS_ROUTES
)
async def delete_service(service_id: uuid.UUID, access: CanWrite, db: DbSession) -> None:
    await crud.delete_service(
        db, workspace_id=access.workspace_id, audit=access.audit, service_id=service_id
    )


# ---- Targets (decision 14): the backend instances a service's traffic is spread over

_TARGET_NOT_FOUND = error_responses({404: "Service or target not found"})
_TARGET_CREATE_CONFLICT = error_responses(
    {409: "URL already used by this service's targets, or the service has the maximum of targets"}
)
_LAST_TARGET = error_responses({409: "A service keeps at least one target"})


@router.get("/{service_id}/targets", responses=_NOT_FOUND)
async def list_targets(
    service_id: uuid.UUID, access: CanRead, db: DbSession, page: Pagination
) -> Page[TargetOut]:
    targets, total = await crud.list_targets(
        db, workspace_id=access.workspace_id, service_id=service_id, page=page
    )
    return Page[TargetOut](items=[TargetOut.model_validate(t) for t in targets], total=total)


@router.post(
    "/{service_id}/targets",
    status_code=status.HTTP_201_CREATED,
    responses=_NOT_FOUND | _TARGET_CREATE_CONFLICT,
)
async def create_target(
    service_id: uuid.UUID, body: TargetCreate, access: CanWrite, db: DbSession
) -> TargetOut:
    target = await crud.create_target(
        db, workspace_id=access.workspace_id, audit=access.audit, service_id=service_id, data=body
    )
    return TargetOut.model_validate(target)


@router.patch("/{service_id}/targets/{target_id}", responses=_TARGET_NOT_FOUND)
async def update_target(
    service_id: uuid.UUID,
    target_id: uuid.UUID,
    body: TargetUpdate,
    access: CanWrite,
    db: DbSession,
) -> TargetOut:
    target = await crud.update_target(
        db,
        workspace_id=access.workspace_id,
        audit=access.audit,
        service_id=service_id,
        target_id=target_id,
        data=body,
    )
    return TargetOut.model_validate(target)


@router.delete(
    "/{service_id}/targets/{target_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_TARGET_NOT_FOUND | _LAST_TARGET,
)
async def delete_target(
    service_id: uuid.UUID, target_id: uuid.UUID, access: CanWrite, db: DbSession
) -> None:
    await crud.delete_target(
        db,
        workspace_id=access.workspace_id,
        audit=access.audit,
        service_id=service_id,
        target_id=target_id,
    )
