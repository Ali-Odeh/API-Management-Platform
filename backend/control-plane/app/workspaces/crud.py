import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import crud as audit
from app.audit.changes import created
from app.audit.context import AuditContext
from app.core.db import flush_or_raise
from app.core.errors import ConflictError, UnprocessableError
from app.core.pagination import PageParams, paginate
from app.core.permissions import Role
from app.members import crud as members_crud
from app.members.models import WorkspaceMember
from app.users import crud as users_crud
from app.workspaces.models import DEFAULT_WORKSPACE_SLUG, Workspace
from app.workspaces.schemas import WorkspaceCreate

WORKSPACE_NOT_FOUND = "Workspace not found"
UQ_WORKSPACE_SLUG = "uq_workspaces_slug"


async def get_workspace_by_slug(db: AsyncSession, slug: str) -> Workspace | None:
    result = await db.scalars(select(Workspace).where(Workspace.slug == slug))
    return result.one_or_none()


async def get_workspace_and_role(
    db: AsyncSession, slug: str, *, user_id: uuid.UUID
) -> tuple[uuid.UUID, Role | None] | None:
    """The workspace's id and the user's role in it (None: not a member), in one query.

    None when no workspace has this slug.
    """
    row = (
        await db.execute(
            select(Workspace.id, WorkspaceMember.role)
            .outerjoin(
                WorkspaceMember,
                (WorkspaceMember.workspace_id == Workspace.id)
                & (WorkspaceMember.user_id == user_id),
            )
            .where(Workspace.slug == slug)
        )
    ).one_or_none()
    if row is None:
        return None
    return row.id, Role(row.role) if row.role is not None else None


async def get_or_create_default_workspace(db: AsyncSession) -> Workspace:
    """Return the `default` workspace, creating it on first run (audited as `system`)."""
    workspace = await get_workspace_by_slug(db, DEFAULT_WORKSPACE_SLUG)
    if workspace is None:
        workspace = Workspace(id=uuid.uuid4(), name="Default", slug=DEFAULT_WORKSPACE_SLUG)
        db.add(workspace)
        _record_created(db, AuditContext.system(), workspace, owner_email=None)
        await db.flush()  # send the INSERT now so workspace.id is usable
    return workspace


async def get_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> Workspace:
    """A workspace the permission check has just found (workspaces are never deleted)."""
    return (await db.scalars(select(Workspace).where(Workspace.id == workspace_id))).one()


async def list_workspaces(db: AsyncSession, *, page: PageParams) -> tuple[list[Workspace], int]:
    """Every workspace, newest first (Platform Admin)."""
    query = select(Workspace).order_by(Workspace.created_at.desc(), Workspace.id)
    return await paginate(db, query, page)


async def list_my_workspaces(
    db: AsyncSession, *, user_id: uuid.UUID, page: PageParams
) -> tuple[list[tuple[Workspace, Role]], int]:
    """The workspaces a person belongs to, with their role in each, newest first."""
    query = (
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(Workspace.created_at.desc(), Workspace.id)
    )
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await db.execute(query.offset(page.offset).limit(page.page_size))
    return [(workspace, Role(role)) for workspace, role in rows.tuples()], total


async def create_workspace(
    db: AsyncSession, *, data: WorkspaceCreate, created_by: uuid.UUID, audit_ctx: AuditContext
) -> Workspace:
    """A new workspace with its first Owner, in one transaction (Platform Admin).

    The owner must already be a user (they signed in once); inviting a new
    person by email comes with MT.4. Their row stays share-locked from the
    "is active?" check to the commit, so a parallel disable either finishes
    first (and this answers 422) or waits until the workspace exists: never
    a new workspace whose only Owner was disabled in between.
    """
    owner = await users_crud.get_by_email_locked(db, data.owner_email)
    if owner is None:
        raise UnprocessableError(
            "No user with this email; they must sign in once first (invitations: MT.4)"
        )
    if not owner.is_active:
        raise UnprocessableError("This user is disabled")

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
    _record_created(db, audit_ctx, workspace, owner_email=owner.email)
    await db.commit()
    await db.refresh(workspace)
    return workspace


def _record_created(
    db: AsyncSession, audit_ctx: AuditContext, workspace: Workspace, *, owner_email: str | None
) -> None:
    audit.record(
        db,
        audit_ctx,
        workspace_id=None,  # a lifecycle event: platform-level, survives a purge
        action="workspace.created",
        target_type="workspace",
        target_id=workspace.id,
        target_name=workspace.slug,
        changes=created({"name": workspace.name, "slug": workspace.slug, "owner": owner_email}),
    )
