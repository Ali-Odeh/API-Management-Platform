"""HTTP layer for workspaces: create and list (Platform Admin), read one, my workspaces."""

from typing import Annotated

from fastapi import APIRouter, status

from app.core.deps import (
    CurrentUser,
    DbSession,
    PlatformAdmin,
    PlatformAudit,
    WorkspaceAccess,
    require_permission,
)
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.core.permissions import Permission
from app.workspaces import crud
from app.workspaces.schemas import MyWorkspaceOut, WorkspaceCreate, WorkspaceOut

router = APIRouter(prefix="/api/v1")

CanRead = Annotated[WorkspaceAccess, require_permission(Permission.WORKSPACE_READ)]


@router.get("/workspaces", tags=["platform"])
async def list_workspaces(
    admin: PlatformAdmin, db: DbSession, page: Pagination
) -> Page[WorkspaceOut]:
    """Every workspace (Platform Admin, with MFA)."""
    workspaces, total = await crud.list_workspaces(db, page=page)
    return Page[WorkspaceOut](
        items=[WorkspaceOut.model_validate(w) for w in workspaces], total=total
    )


@router.post(
    "/workspaces",
    tags=["platform"],
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        {409: "Slug already taken", 422: "Invalid input, or the owner isn't a (active) user"}
    ),
)
async def create_workspace(
    body: WorkspaceCreate, admin: PlatformAdmin, audit: PlatformAudit, db: DbSession
) -> WorkspaceOut:
    """A new workspace and its first Owner (Platform Admin, with MFA)."""
    workspace = await crud.create_workspace(db, data=body, created_by=admin.id, audit_ctx=audit)
    return WorkspaceOut.model_validate(workspace)


@router.get(
    "/workspaces/{slug}",
    tags=["workspaces"],
    responses=error_responses({404: "Workspace not found"}),
)
async def get_workspace(access: CanRead, db: DbSession) -> WorkspaceOut:
    workspace = await crud.get_workspace(db, access.workspace_id)
    return WorkspaceOut.model_validate(workspace)


@router.get("/me/workspaces", tags=["me"])
async def my_workspaces(user: CurrentUser, db: DbSession, page: Pagination) -> Page[MyWorkspaceOut]:
    """The workspaces I belong to and my role in each."""
    rows, total = await crud.list_my_workspaces(db, user_id=user.id, page=page)
    items = [
        MyWorkspaceOut(id=w.id, name=w.name, slug=w.slug, created_at=w.created_at, role=role)
        for w, role in rows
    ]
    return Page[MyWorkspaceOut](items=items, total=total)
