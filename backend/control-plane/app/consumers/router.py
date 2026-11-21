"""HTTP layer for consumers: parse input, call crud, shape output. No SQL here."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.consumers import crud
from app.consumers.models import ConsumerStatus
from app.consumers.schemas import ConsumerCreate, ConsumerOut, ConsumerUpdate
from app.core.deps import DbSession, WorkspaceAccess, require_permission
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.core.permissions import Permission

router = APIRouter(prefix="/api/v1/workspaces/{slug}/consumers", tags=["consumers"])

CanRead = Annotated[WorkspaceAccess, require_permission(Permission.CONSUMERS_READ)]
CanWrite = Annotated[WorkspaceAccess, require_permission(Permission.CONSUMERS_WRITE)]

_NOT_FOUND = error_responses({404: "Consumer not found"})
_CONFLICT = error_responses({409: "Name already used in this workspace"})


@router.get("")
async def list_consumers(
    access: CanRead,
    db: DbSession,
    page: Pagination,
    # Named status_filter in Python so it doesn't hide fastapi's `status` module.
    status_filter: Annotated[
        ConsumerStatus | None, Query(alias="status", description="Only active or only blocked")
    ] = None,
) -> Page[ConsumerOut]:
    consumers, total = await crud.list_consumers(
        db, workspace_id=access.workspace_id, page=page, status=status_filter
    )
    return Page[ConsumerOut](items=[ConsumerOut.model_validate(c) for c in consumers], total=total)


@router.post("", status_code=status.HTTP_201_CREATED, responses=_CONFLICT)
async def create_consumer(body: ConsumerCreate, access: CanWrite, db: DbSession) -> ConsumerOut:
    consumer = await crud.create_consumer(
        db, workspace_id=access.workspace_id, audit=access.audit, data=body
    )
    return ConsumerOut.model_validate(consumer)


@router.get("/{consumer_id}", responses=_NOT_FOUND)
async def get_consumer(consumer_id: uuid.UUID, access: CanRead, db: DbSession) -> ConsumerOut:
    consumer = await crud.get_consumer(
        db, workspace_id=access.workspace_id, consumer_id=consumer_id
    )
    return ConsumerOut.model_validate(consumer)


@router.patch("/{consumer_id}", responses=_NOT_FOUND | _CONFLICT)
async def update_consumer(
    consumer_id: uuid.UUID, body: ConsumerUpdate, access: CanWrite, db: DbSession
) -> ConsumerOut:
    """Change any field. To block a consumer: `{"status": "blocked"}`."""
    consumer = await crud.update_consumer(
        db, workspace_id=access.workspace_id, audit=access.audit, consumer_id=consumer_id, data=body
    )
    return ConsumerOut.model_validate(consumer)


@router.delete("/{consumer_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
async def delete_consumer(consumer_id: uuid.UUID, access: CanWrite, db: DbSession) -> None:
    """Delete a consumer **and all of its API keys**. This can't be undone."""
    await crud.delete_consumer(
        db, workspace_id=access.workspace_id, audit=access.audit, consumer_id=consumer_id
    )
