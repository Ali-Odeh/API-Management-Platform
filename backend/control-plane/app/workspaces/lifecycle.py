"""A workspace's life: suspend, unsuspend, delete, restore (decision 10).

    active ──suspend──> suspended ──unsuspend──> active
    active or suspended ──delete──> deleted ──restore (before purge_after)──> active
    deleted ──purge job, after purge_after──> purged (for good)

Each change locks the workspace row first (SELECT ... FOR UPDATE), so two
transitions (or a restore and the purge job) run one after the other and the
second sees the first's result. A refusal leaves the lock to the request's
session, which rolls back when it closes. A transition from the wrong status is
`409 workspace_<status>`, naming where the workspace is now. `default` is
never suspended or deleted (requests without a subdomain go to it).

Lifecycle events are platform-level (`workspace_id` NULL), so they survive
the purge of the workspace's own log.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import crud as audit
from app.audit.changes import Changes, snapshot, updated
from app.audit.context import AuditContext
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.workspaces.crud import WORKSPACE_NOT_FOUND
from app.workspaces.models import DEFAULT_WORKSPACE_SLUG, Workspace, WorkspaceStatus

DEFAULT_WORKSPACE = "default_workspace"
RESTORE_PERIOD_OVER = "restore_period_over"
# What a lifecycle event records.
LIFECYCLE_FIELDS = ("status", "status_reason", "deleted_at", "purge_after")


async def suspend(
    db: AsyncSession, slug: str, *, reason: str, audit_ctx: AuditContext
) -> Workspace:
    """Stop the workspace's traffic and make it read-only for its members."""
    workspace = await _locked(db, select(Workspace).where(Workspace.slug == slug))
    _never_default(workspace)
    _expect(workspace, WorkspaceStatus.ACTIVE)
    return await _move(
        db, audit_ctx, workspace, "workspace.suspended",
        status=WorkspaceStatus.SUSPENDED, status_reason=reason,
    )  # fmt: skip


async def unsuspend(db: AsyncSession, slug: str, *, audit_ctx: AuditContext) -> Workspace:
    workspace = await _locked(db, select(Workspace).where(Workspace.slug == slug))
    _expect(workspace, WorkspaceStatus.SUSPENDED)
    return await _move(
        db, audit_ctx, workspace, "workspace.unsuspended",
        status=WorkspaceStatus.ACTIVE, status_reason=None,
    )  # fmt: skip


async def delete(
    db: AsyncSession, workspace_id: uuid.UUID, *, confirm: str, audit_ctx: AuditContext
) -> None:
    """Hide the workspace from its members, restorable for PURGE_AFTER_DAYS.

    `confirm` must repeat the slug (as on GitHub), so a wrong URL or a
    script can't delete the wrong workspace. Only a member's or the Platform
    Admin's permission check leads here, so it is found by id.
    """
    workspace = await _locked(db, select(Workspace).where(Workspace.id == workspace_id))
    _never_default(workspace)
    if confirm != workspace.slug:
        raise UnprocessableError("Type the workspace's slug in `confirm` to delete it")
    if workspace.status.is_gone:  # deleted by a parallel request a moment ago
        raise NotFoundError(WORKSPACE_NOT_FOUND)
    now = datetime.now(UTC)
    await _move(
        db, audit_ctx, workspace, "workspace.deleted",
        status=WorkspaceStatus.DELETED, status_reason=None,
        deleted_at=now, purge_after=now + timedelta(days=get_settings().purge_after_days),
    )  # fmt: skip


async def restore(db: AsyncSession, slug: str, *, audit_ctx: AuditContext) -> Workspace:
    """Bring a deleted workspace back, active, before its purge.

    Active even if it was suspended when it was deleted: only the Platform
    Admin can delete a suspended workspace, and only they restore, so they
    suspend it again if it still needs to be.
    """
    workspace = await _locked(db, select(Workspace).where(Workspace.slug == slug))
    _expect(workspace, WorkspaceStatus.DELETED)
    assert workspace.purge_after is not None  # a CHECK guarantees it for deleted rows
    if workspace.purge_after <= datetime.now(UTC):
        raise ConflictError(RESTORE_PERIOD_OVER)  # the purge job may be on it already
    return await _move(
        db, audit_ctx, workspace, "workspace.restored",
        status=WorkspaceStatus.ACTIVE, deleted_at=None, purge_after=None,
    )  # fmt: skip


async def _locked(db: AsyncSession, query: Select[tuple[Workspace]]) -> Workspace:
    workspace = await db.scalar(query.with_for_update().execution_options(populate_existing=True))
    if workspace is None:
        raise NotFoundError(WORKSPACE_NOT_FOUND)
    return workspace


def _never_default(workspace: Workspace) -> None:
    if workspace.slug == DEFAULT_WORKSPACE_SLUG:
        raise ConflictError(DEFAULT_WORKSPACE)


def _expect(workspace: Workspace, status: WorkspaceStatus) -> None:
    if workspace.status != status:
        raise ConflictError(f"workspace_{workspace.status}")


async def _move(
    db: AsyncSession,
    audit_ctx: AuditContext,
    workspace: Workspace,
    action: str,
    **values: object,
) -> Workspace:
    before = snapshot(workspace, LIFECYCLE_FIELDS)
    for field, value in values.items():
        setattr(workspace, field, value)
    changes = updated(before, snapshot(workspace, LIFECYCLE_FIELDS))
    _record(db, audit_ctx, workspace, action, changes)
    await db.commit()
    await db.refresh(workspace)
    return workspace


def _record(
    db: AsyncSession, audit_ctx: AuditContext, workspace: Workspace, action: str, changes: Changes
) -> None:
    """A platform-level event about the workspace: it outlives the workspace's own log."""
    audit.record(
        db,
        audit_ctx,
        workspace_id=None,
        action=action,
        target_type="workspace",
        target_id=workspace.id,
        target_name=workspace.slug,
        changes=changes,
    )
