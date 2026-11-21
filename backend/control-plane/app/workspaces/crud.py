import uuid
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import crud as audit
from app.audit.changes import Changes, created, snapshot, updated
from app.audit.context import AuditContext
from app.core.db import flush_or_raise
from app.core.errors import ConflictError
from app.core.pagination import PageParams, paginate
from app.core.permissions import Role
from app.gotrue.client import GoTrueAdmin
from app.members import crud as members_crud
from app.members.models import WorkspaceMember
from app.users import crud as users_crud
from app.workspaces.models import DEFAULT_WORKSPACE_SLUG, Workspace, WorkspaceStatus
from app.workspaces.schemas import WorkspaceCreate, WorkspaceUpdate

WORKSPACE_NOT_FOUND = "Workspace not found"
UQ_WORKSPACE_SLUG = "uq_workspaces_slug"


async def get_workspace_by_slug(db: AsyncSession, slug: str) -> Workspace | None:
    result = await db.scalars(select(Workspace).where(Workspace.slug == slug))
    return result.one_or_none()


class Membership(NamedTuple):
    """A workspace as the permission check needs it, and the caller's role there."""

    workspace_id: uuid.UUID
    status: WorkspaceStatus
    role: Role | None  # None: not a member


async def get_workspace_and_role(
    db: AsyncSession, slug: str, *, user_id: uuid.UUID
) -> Membership | None:
    """The workspace, its status and the user's role in it, in one query.

    None when no workspace has this slug, or it was deleted (or purged):
    a deleted workspace is gone for everyone acting in it (decision 10).
    """
    row = (
        await db.execute(
            select(Workspace.id, Workspace.status, WorkspaceMember.role)
            .outerjoin(
                WorkspaceMember,
                (WorkspaceMember.workspace_id == Workspace.id)
                & (WorkspaceMember.user_id == user_id),
            )
            .where(Workspace.slug == slug)
        )
    ).one_or_none()
    if row is None or row.status.is_gone:
        return None
    return Membership(row.id, row.status, Role(row.role) if row.role is not None else None)


async def get_or_create_default_workspace(db: AsyncSession) -> Workspace:
    """Return the `default` workspace, creating it on first run (audited as `system`)."""
    workspace = await get_workspace_by_slug(db, DEFAULT_WORKSPACE_SLUG)
    if workspace is None:
        workspace = Workspace(id=uuid.uuid4(), name="Default", slug=DEFAULT_WORKSPACE_SLUG)
        db.add(workspace)
        _record_created(db, AuditContext.system(), workspace, owner_email=None, invited=False)
        await db.flush()  # send the INSERT now so workspace.id is usable
    return workspace


async def get_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> Workspace:
    """A workspace the permission check has just found (rows are never deleted)."""
    return (await db.scalars(select(Workspace).where(Workspace.id == workspace_id))).one()


async def list_workspaces(
    db: AsyncSession, *, page: PageParams, status: WorkspaceStatus | None = None
) -> tuple[list[Workspace], int]:
    """Every workspace, deleted and purged ones too, newest first (Platform Admin)."""
    query = select(Workspace).order_by(Workspace.created_at.desc(), Workspace.id)
    if status is not None:
        query = query.where(Workspace.status == status)
    return await paginate(db, query, page)


async def list_my_workspaces(
    db: AsyncSession, *, user_id: uuid.UUID, page: PageParams
) -> tuple[list[tuple[Workspace, Role]], int]:
    """The workspaces a person belongs to, with their role in each, newest first.

    Suspended ones included (members may still read them); deleted ones are gone.
    """
    query = (
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == user_id,
            Workspace.status.in_([WorkspaceStatus.ACTIVE, WorkspaceStatus.SUSPENDED]),
        )
        .order_by(Workspace.created_at.desc(), Workspace.id)
    )
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await db.execute(query.offset(page.offset).limit(page.page_size))
    return [(workspace, Role(role)) for workspace, role in rows.tuples()], total


async def create_workspace(
    db: AsyncSession,
    *,
    gotrue: GoTrueAdmin,
    data: WorkspaceCreate,
    created_by: uuid.UUID,
    audit_ctx: AuditContext,
) -> Workspace:
    """A new workspace with its first Owner, in one transaction (Platform Admin).

    A new person is invited by email and owns the workspace from their first
    login (decision 8). A known owner's row stays share-locked until the
    commit, so a parallel disable either finishes first (422) or waits until
    the workspace exists: never a new workspace whose only Owner was
    disabled in between.
    """
    owner, invited = await users_crud.get_or_invite(db, gotrue, data.owner_email)

    workspace = Workspace(name=data.name, slug=data.slug)
    db.add(workspace)
    # Sends the INSERT now: a taken slug is a 409, and the id is known for the membership.
    await flush_or_raise(
        db, {UQ_WORKSPACE_SLUG: ConflictError(f"Slug '{data.slug}' is already taken")}
    )
    members_crud.add_member(
        db, workspace.id, user_id=owner.id, role=Role.OWNER, added_by=created_by
    )
    # One event for the workspace and its first Owner, platform-level (PLAN.md section 6).
    _record_created(db, audit_ctx, workspace, owner_email=owner.email, invited=invited)
    await db.commit()
    await db.refresh(workspace)
    return workspace


async def rename_workspace(
    db: AsyncSession, workspace_id: uuid.UUID, *, data: WorkspaceUpdate, audit_ctx: AuditContext
) -> Workspace:
    """A new name (Owner); the slug never changes. A workspace-level event:
    unlike the lifecycle, the workspace's own members see it in their log."""
    workspace = await get_workspace(db, workspace_id)
    before = snapshot(workspace, ("name",))
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(workspace, field, value)
    if changes := updated(before, snapshot(workspace, ("name",))):
        record_in_workspace(db, audit_ctx, workspace, "workspace.update", changes)
        await db.commit()
        await db.refresh(workspace)
    return workspace


def record_in_workspace(
    db: AsyncSession, audit_ctx: AuditContext, workspace: Workspace, action: str, changes: Changes
) -> None:
    """An event about the workspace in its own log, seen by its members."""
    audit.record(
        db,
        audit_ctx,
        workspace_id=workspace.id,
        action=action,
        target_type="workspace",
        target_id=workspace.id,
        target_name=workspace.slug,
        changes=changes,
    )


def _record_created(
    db: AsyncSession,
    audit_ctx: AuditContext,
    workspace: Workspace,
    *,
    owner_email: str | None,
    invited: bool,
) -> None:
    audit.record(
        db,
        audit_ctx,
        workspace_id=None,  # a lifecycle event: platform-level, survives a purge
        action="workspace.created",
        target_type="workspace",
        target_id=workspace.id,
        target_name=workspace.slug,
        changes=created(
            {"name": workspace.name, "slug": workspace.slug, "owner": owner_email}
            | ({"invited": True} if invited else {})
        ),
    )
