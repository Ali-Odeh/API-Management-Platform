"""HTTP layer for the audit log: a workspace's log and the whole platform's."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.audit import crud
from app.audit.schemas import AuditEventOut, Filters
from app.core.deps import DbSession, PlatformAdmin, WorkspaceAccess, require_permission
from app.core.pagination import Page, Pagination
from app.core.permissions import Permission

router = APIRouter(prefix="/api/v1", tags=["audit"])

CanRead = Annotated[WorkspaceAccess, require_permission(Permission.AUDIT_READ)]


@router.get("/workspaces/{slug}/audit-events")
async def list_workspace_events(
    access: CanRead, db: DbSession, page: Pagination, filters: Filters
) -> Page[AuditEventOut]:
    """Who changed what in this workspace, the Platform Admin's changes included."""
    events, total = await crud.list_workspace_events(
        db, access.workspace_id, filters=filters, page=page
    )
    return Page[AuditEventOut](items=[AuditEventOut.model_validate(e) for e in events], total=total)


@router.get("/audit-events", tags=["platform"])
async def list_all_events(
    admin: PlatformAdmin,
    db: DbSession,
    page: Pagination,
    filters: Filters,
    workspace_id: Annotated[uuid.UUID | None, Query(description="Only this workspace")] = None,
) -> Page[AuditEventOut]:
    """Every event, platform-level ones included (Platform Admin, with MFA)."""
    events, total = await crud.list_all_events(
        db, filters=filters, page=page, workspace_id=workspace_id
    )
    return Page[AuditEventOut](items=[AuditEventOut.model_validate(e) for e in events], total=total)
