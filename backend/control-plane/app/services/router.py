"""HTTP layer for services: parse input, call crud, shape output. No SQL here."""

import uuid

from fastapi import APIRouter, status

from app.core.deps import CurrentAdmin, DbSession
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.services import crud
from app.services.schemas import ServiceCreate, ServiceOut, ServiceUpdate

router = APIRouter(prefix="/api/v1/services", tags=["services"])

_NOT_FOUND = error_responses({404: "Service not found"})
_CONFLICT = error_responses({409: "Name already used in this workspace"})
_HAS_ROUTES = error_responses({409: "Service still has routes"})


@router.get("")
async def list_services(admin: CurrentAdmin, db: DbSession, page: Pagination) -> Page[ServiceOut]:
    services, total = await crud.list_services(db, workspace_id=admin.workspace_id, page=page)
    return Page[ServiceOut](items=[ServiceOut.model_validate(s) for s in services], total=total)


@router.post("", status_code=status.HTTP_201_CREATED, responses=_CONFLICT)
async def create_service(body: ServiceCreate, admin: CurrentAdmin, db: DbSession) -> ServiceOut:
    service = await crud.create_service(db, workspace_id=admin.workspace_id, data=body)
    return ServiceOut.model_validate(service)


@router.get("/{service_id}", responses=_NOT_FOUND)
async def get_service(service_id: uuid.UUID, admin: CurrentAdmin, db: DbSession) -> ServiceOut:
    service = await crud.get_service(db, workspace_id=admin.workspace_id, service_id=service_id)
    return ServiceOut.model_validate(service)


@router.patch("/{service_id}", responses=_NOT_FOUND | _CONFLICT)
async def update_service(
    service_id: uuid.UUID, body: ServiceUpdate, admin: CurrentAdmin, db: DbSession
) -> ServiceOut:
    service = await crud.update_service(
        db, workspace_id=admin.workspace_id, service_id=service_id, data=body
    )
    return ServiceOut.model_validate(service)


@router.delete(
    "/{service_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND | _HAS_ROUTES
)
async def delete_service(service_id: uuid.UUID, admin: CurrentAdmin, db: DbSession) -> None:
    await crud.delete_service(db, workspace_id=admin.workspace_id, service_id=service_id)
