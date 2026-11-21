"""All database work for memberships, and the owner rules (decision 9).

A workspace always keeps at least one active Owner: demoting, removing or the
leaving of the last one is refused (409 last_owner). Every change that could
take an Owner away first locks all of the workspace's owner rows, in one
order, so two Owners demoting each other at the same moment run one after the
other, and the second sees there is nobody left to keep.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import crud as audit
from app.audit.changes import Changes
from app.audit.context import AuditContext
from app.audit.models import ActorType
from app.core.db import commit_or_raise
from app.core.errors import ConflictError, NotFoundError
from app.core.pagination import PageParams
from app.core.permissions import Role
from app.gotrue.client import GoTrueAdmin
from app.members.models import UQ_MEMBER, WorkspaceMember
from app.members.schemas import MemberCreate, MemberUpdate
from app.users import crud as users_crud
from app.users.models import User

LAST_OWNER = "last_owner"
MEMBER_NOT_FOUND = "Member not found"


def add_member(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    role: Role,
    added_by: uuid.UUID | None,
) -> WorkspaceMember:
    """Stage a membership; the caller commits it with the rest of its change."""
    member = WorkspaceMember(
        id=uuid.uuid4(), workspace_id=workspace_id, user_id=user_id, role=role, added_by=added_by
    )
    db.add(member)
    return member


async def has_owner(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    found = await db.scalar(
        select(WorkspaceMember.id)
        .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == Role.OWNER)
        .limit(1)
    )
    return found is not None


async def list_members(
    db: AsyncSession, workspace_id: uuid.UUID, *, page: PageParams
) -> tuple[list[tuple[WorkspaceMember, User]], int]:
    """One page of members with their person: Owners first, then by email."""
    total = await db.scalar(
        select(func.count()).where(WorkspaceMember.workspace_id == workspace_id)
    )
    rows = await db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.role != Role.OWNER, User.email)
        .offset(page.offset)
        .limit(page.page_size)
    )
    return list(rows.tuples()), total or 0


async def add_member_by_email(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    gotrue: GoTrueAdmin,
    data: MemberCreate,
    audit_ctx: AuditContext,
) -> tuple[WorkspaceMember, User, bool]:
    """Add a person (decision 8): a known one at once, a new one by invitation.

    Returns (member, user, invited). Already a member: 409.
    """
    user, invited = await users_crud.get_or_invite(db, gotrue, data.email)
    member = add_member(
        db, workspace_id, user_id=user.id, role=data.role, added_by=audit_ctx.actor_user_id
    )
    changes: Changes = {"role": [None, data.role.value]}
    if invited:
        changes["invited"] = [None, True]
    action = (
        "member.owner_assigned_by_platform"
        if _platform_owner(audit_ctx, data.role)
        else ("member.add")
    )
    _record(db, audit_ctx, member, user, action, changes)
    await commit_or_raise(db, {UQ_MEMBER: ConflictError("Already a member of this workspace")})
    await db.refresh(member)
    return member, user, invited


async def change_role(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    data: MemberUpdate,
    audit_ctx: AuditContext,
) -> tuple[WorkspaceMember, User]:
    """Change a member's role (Owner). Nobody changes their own role (it
    prevents demoting yourself by mistake), and the last active Owner can't be
    demoted."""
    if user_id == audit_ctx.actor_user_id:
        raise ConflictError("You can't change your own role")
    owners = await _lock_owners(db, workspace_id)
    member, user = await _get_locked(db, workspace_id, user_id)
    if data.role is None or data.role == member.role:  # nothing to change
        await db.commit()  # just ends the transaction and its locks
        return member, user
    if member.role == Role.OWNER:
        _keep_an_owner(owners, losing=user_id)
    action = (
        "member.owner_assigned_by_platform"
        if _platform_owner(audit_ctx, data.role)
        else ("member.role_change")
    )
    _record(db, audit_ctx, member, user, action, {"role": [member.role, data.role.value]})
    member.role = data.role
    await db.commit()
    await db.refresh(member)
    return member, user


async def remove_member(
    db: AsyncSession, workspace_id: uuid.UUID, *, user_id: uuid.UUID, audit_ctx: AuditContext
) -> None:
    """Remove a member (Owner); never the last active Owner."""
    await _take_away(db, workspace_id, user_id, audit_ctx, "member.remove")


async def leave(
    db: AsyncSession, workspace_id: uuid.UUID, *, user_id: uuid.UUID, audit_ctx: AuditContext
) -> None:
    """Leave a workspace (any member); the last active Owner can't."""
    await _take_away(db, workspace_id, user_id, audit_ctx, "member.leave")


async def _take_away(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    audit_ctx: AuditContext,
    action: str,
) -> None:
    owners = await _lock_owners(db, workspace_id)
    member, user = await _get_locked(db, workspace_id, user_id)
    if member.role == Role.OWNER:
        _keep_an_owner(owners, losing=user_id)
    _record(db, audit_ctx, member, user, action, {"role": [member.role, None]})
    await db.delete(member)
    await db.commit()


async def _lock_owners(db: AsyncSession, workspace_id: uuid.UUID) -> dict[uuid.UUID, bool]:
    """Lock every owner row of the workspace (SELECT ... FOR UPDATE, in id order,
    so parallel changes queue instead of deadlocking): owner -> is active."""
    rows = await db.execute(
        select(WorkspaceMember.user_id, User.is_active)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == Role.OWNER)
        .order_by(WorkspaceMember.id)
        .with_for_update(of=WorkspaceMember)
    )
    return dict(rows.tuples().all())


def _keep_an_owner(owners: dict[uuid.UUID, bool], *, losing: uuid.UUID) -> None:
    """At least one active (human) Owner must stay (decision 9): a disabled one
    can't manage anything, so it doesn't count."""
    if not any(active for owner, active in owners.items() if owner != losing):
        raise ConflictError(LAST_OWNER)


async def _get_locked(
    db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[WorkspaceMember, User]:
    row = (
        await db.execute(
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id)
            .with_for_update(of=WorkspaceMember)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError(MEMBER_NOT_FOUND)
    return row[0], row[1]


def _platform_owner(audit_ctx: AuditContext, role: Role) -> bool:
    """A Platform Admin making someone Owner is the safety net (decision 9): it
    gets its own action, so the workspace's Owners see it at a glance."""
    return role == Role.OWNER and audit_ctx.actor_type == ActorType.PLATFORM_ADMIN


def _record(
    db: AsyncSession,
    audit_ctx: AuditContext,
    member: WorkspaceMember,
    user: User,
    action: str,
    changes: Changes,
) -> None:
    audit.record(
        db,
        audit_ctx,
        workspace_id=member.workspace_id,
        action=action,
        target_type="member",
        target_id=user.id,
        target_name=user.email,
        changes=changes,
    )
