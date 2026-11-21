"""HTTP layer for workspaces: create, list and the lifecycle (Platform Admin),
read, rename and delete (members, by permission), my workspaces."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.deps import (
    CurrentUser,
    DbSession,
    GoTrue,
    PlatformAdmin,
    PlatformAudit,
    WorkspaceAccess,
    require_permission,
)
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.core.permissions import Permission
from app.workspaces import crud, lifecycle, limits
from app.workspaces.models import LIMIT_COLUMNS, Workspace, WorkspaceStatus
from app.workspaces.schemas import (
    LimitOverrides,
    Limits,
    MyWorkspaceOut,
    Suspension,
    Usage,
    WorkspaceCreate,
    WorkspaceDelete,
    WorkspaceDetail,
    WorkspaceOut,
    WorkspaceUpdate,
)

router = APIRouter(prefix="/api/v1")

CanRead = Annotated[WorkspaceAccess, require_permission(Permission.WORKSPACE_READ)]
CanWrite = Annotated[WorkspaceAccess, require_permission(Permission.WORKSPACE_WRITE)]
CanDelete = Annotated[WorkspaceAccess, require_permission(Permission.WORKSPACE_DELETE)]

_NOT_FOUND = error_responses({404: "Workspace not found"})


def _transition(conflict: str) -> dict[int | str, dict[str, object]]:
    return error_responses({404: "Workspace not found", 409: conflict})


def workspace_out(workspace: Workspace) -> WorkspaceOut:
    return WorkspaceOut(
        id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        status=workspace.status,
        status_reason=workspace.status_reason,
        deleted_at=workspace.deleted_at,
        purge_after=workspace.purge_after,
        limits=Limits(**limits.effective_limits(workspace)),
        limit_overrides=LimitOverrides(**{c: getattr(workspace, c) for c in LIMIT_COLUMNS}),
        created_at=workspace.created_at,
    )


# ---- Platform Admin (with MFA): every workspace, and its life


@router.get("/workspaces", tags=["platform"])
async def list_workspaces(
    admin: PlatformAdmin,
    db: DbSession,
    page: Pagination,
    status: Annotated[WorkspaceStatus | None, Query(description="Only this status")] = None,
) -> Page[WorkspaceOut]:
    """Every workspace, deleted and purged ones included (Platform Admin, with MFA)."""
    workspaces, total = await crud.list_workspaces(db, page=page, status=status)
    return Page[WorkspaceOut](items=[workspace_out(w) for w in workspaces], total=total)


@router.post(
    "/workspaces",
    tags=["platform"],
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        {409: "Slug already taken (also by a deleted or purged workspace)",
         422: "Invalid input, or the owner is a disabled user"}
    ),
)  # fmt: skip
async def create_workspace(
    body: WorkspaceCreate,
    admin: PlatformAdmin,
    audit: PlatformAudit,
    db: DbSession,
    gotrue: GoTrue,
) -> WorkspaceOut:
    """A new workspace and its first Owner (Platform Admin, with MFA)."""
    workspace = await crud.create_workspace(
        db, gotrue=gotrue, data=body, created_by=admin.id, audit_ctx=audit
    )
    return workspace_out(workspace)


@router.post(
    "/workspaces/{slug}/suspend",
    tags=["platform"],
    responses=_transition("default_workspace, or workspace_<status>: only an active one"),
)
async def suspend_workspace(
    slug: str, body: Suspension, admin: PlatformAdmin, audit: PlatformAudit, db: DbSession
) -> WorkspaceOut:
    """Refuse the workspace's traffic and make it read-only for its members."""
    workspace = await lifecycle.suspend(db, slug, reason=body.reason, audit_ctx=audit)
    return workspace_out(workspace)


@router.post(
    "/workspaces/{slug}/unsuspend",
    tags=["platform"],
    responses=_transition("workspace_<status>: only a suspended one"),
)
async def unsuspend_workspace(
    slug: str, admin: PlatformAdmin, audit: PlatformAudit, db: DbSession
) -> WorkspaceOut:
    workspace = await lifecycle.unsuspend(db, slug, audit_ctx=audit)
    return workspace_out(workspace)


@router.post(
    "/workspaces/{slug}/restore",
    tags=["platform"],
    responses=_transition(
        "workspace_<status>: only a deleted one; restore_period_over: past its purge_after"
    ),
)
async def restore_workspace(
    slug: str, admin: PlatformAdmin, audit: PlatformAudit, db: DbSession
) -> WorkspaceOut:
    """Bring a deleted workspace back (active) before it is purged."""
    workspace = await lifecycle.restore(db, slug, audit_ctx=audit)
    return workspace_out(workspace)


# ---- Members, by permission


@router.get("/workspaces/{slug}", tags=["workspaces"], responses=_NOT_FOUND)
async def get_workspace(access: CanRead, db: DbSession) -> WorkspaceDetail:
    """The workspace, its limits and how much of each quota it uses."""
    workspace = await crud.get_workspace(db, access.workspace_id)
    usage = Usage(**await limits.usage(db, access.workspace_id))
    return WorkspaceDetail(**workspace_out(workspace).model_dump(), usage=usage)


@router.patch("/workspaces/{slug}", tags=["workspaces"], responses=_NOT_FOUND)
async def rename_workspace(body: WorkspaceUpdate, access: CanWrite, db: DbSession) -> WorkspaceOut:
    """Change the name; the slug (the gateway subdomain) never changes."""
    workspace = await crud.rename_workspace(
        db, access.workspace_id, data=body, audit_ctx=access.audit
    )
    return workspace_out(workspace)


@router.delete(
    "/workspaces/{slug}",
    tags=["workspaces"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(
        {404: "Workspace not found", 409: "default_workspace: it can't be deleted",
         422: "`confirm` isn't the workspace's slug"}
    ),
)  # fmt: skip
async def delete_workspace(body: WorkspaceDelete, access: CanDelete, db: DbSession) -> None:
    """Delete the workspace: hidden from its members at once, restorable by the
    Platform Admin until `purge_after`, then purged for good."""
    await lifecycle.delete(db, access.workspace_id, confirm=body.confirm, audit_ctx=access.audit)


@router.get("/me/workspaces", tags=["me"])
async def my_workspaces(user: CurrentUser, db: DbSession, page: Pagination) -> Page[MyWorkspaceOut]:
    """The workspaces I belong to (suspended ones included) and my role in each."""
    rows, total = await crud.list_my_workspaces(db, user_id=user.id, page=page)
    items = [MyWorkspaceOut(**workspace_out(w).model_dump(), role=role) for w, role in rows]
    return Page[MyWorkspaceOut](items=items, total=total)
