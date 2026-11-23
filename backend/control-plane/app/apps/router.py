"""HTTP layer for a workspace's apps (members, by permission): the approval queue,
approve and reject. Developers make and use apps through the portal API."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.apps import crud
from app.apps.crud import AppView
from app.apps.models import AppStatus
from app.apps.schemas import ApiRef, AppAdminOut, AppDecision, PlanRef
from app.core.deps import DbSession, WorkspaceAccess, require_permission
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.core.permissions import Permission

router = APIRouter(prefix="/api/v1/workspaces/{slug}/apps", tags=["apps"])

CanRead = Annotated[WorkspaceAccess, require_permission(Permission.APPS_READ)]
CanWrite = Annotated[WorkspaceAccess, require_permission(Permission.APPS_WRITE)]

_NOT_FOUND = error_responses({404: "App not found"})


def app_admin_out(view: AppView) -> AppAdminOut:
    return AppAdminOut(
        id=view.app.id,
        name=view.app.name,
        status=view.app.status,
        developer_email=view.developer_email,
        consumer_id=view.app.consumer_id,
        api=ApiRef.model_validate(view.api),
        plan=PlanRef.model_validate(view.plan),
        created_at=view.app.created_at,
        updated_at=view.app.updated_at,
    )


@router.get("")
async def list_apps(
    access: CanRead,
    db: DbSession,
    page: Pagination,
    status: Annotated[AppStatus | None, Query(description="`pending`: the approval queue")] = None,
) -> Page[AppAdminOut]:
    """Developers' apps in this workspace, newest first."""
    views, total = await crud.list_apps(db, access.workspace_id, page=page, status=status)
    return Page[AppAdminOut](items=[app_admin_out(v) for v in views], total=total)


@router.get("/{app_id}", responses=_NOT_FOUND)
async def get_app(app_id: uuid.UUID, access: CanRead, db: DbSession) -> AppAdminOut:
    return app_admin_out(await crud.get_app(db, access.workspace_id, app_id=app_id))


@router.patch("/{app_id}", responses=_NOT_FOUND)
async def decide(
    app_id: uuid.UUID, body: AppDecision, access: CanWrite, db: DbSession
) -> AppAdminOut:
    """Approve (`{"status": "approved"}`: its keys work) or reject an app (they stop working)."""
    view = await crud.decide(
        db, access.workspace_id, app_id=app_id, data=body, audit_ctx=access.audit
    )
    return app_admin_out(view)
