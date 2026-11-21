"""All database work for memberships (adding, listing, changing and removing
members comes with MT.4; MT.2 needs the first owner)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Role
from app.members.models import WorkspaceMember


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
        workspace_id=workspace_id, user_id=user_id, role=role, added_by=added_by
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
