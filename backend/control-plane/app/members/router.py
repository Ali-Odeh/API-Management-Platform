"""HTTP layer for members: an Owner builds the team; anyone may leave (decisions 8, 9)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Request, status

from app.audit.models import ActorType
from app.core.deps import (
    CurrentUser,
    DbSession,
    GoTrue,
    WorkspaceAccess,
    request_audit,
    require_permission,
)
from app.core.errors import NotFoundError
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.core.permissions import Permission, Role
from app.members import crud
from app.members.models import WorkspaceMember
from app.members.schemas import MemberAdded, MemberCreate, MemberOut, MemberUpdate
from app.users.models import User
from app.workspaces import crud as workspaces_crud
from app.workspaces.crud import WORKSPACE_NOT_FOUND

router = APIRouter(prefix="/api/v1", tags=["members"])

CanRead = Annotated[WorkspaceAccess, require_permission(Permission.MEMBERS_READ)]
CanWrite = Annotated[WorkspaceAccess, require_permission(Permission.MEMBERS_WRITE)]

_NOT_FOUND = error_responses({404: "Member not found"})
_LAST_OWNER = error_responses(
    {409: "last_owner: the workspace would have no active Owner left, or your own role"}
)


def _out(member: WorkspaceMember, user: User) -> MemberOut:
    return MemberOut(
        user_id=user.id,
        email=user.email,
        role=Role(member.role),
        is_active=user.is_active,
        added_by=member.added_by,
        created_at=member.created_at,
    )


@router.get("/workspaces/{slug}/members")
async def list_members(access: CanRead, db: DbSession, page: Pagination) -> Page[MemberOut]:
    rows, total = await crud.list_members(db, access.workspace_id, page=page)
    return Page[MemberOut](items=[_out(m, u) for m, u in rows], total=total)


@router.post(
    "/workspaces/{slug}/members",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        {409: "Already a member", 422: "Invalid input, or the person is disabled"}
    ),
)
async def add_member(
    body: MemberCreate, access: CanWrite, db: DbSession, gotrue: GoTrue
) -> MemberAdded:
    """Add a person by email and role: a user at once, someone new by invitation."""
    member, user, invited = await crud.add_member_by_email(
        db, access.workspace_id, gotrue=gotrue, data=body, audit_ctx=access.audit
    )
    return MemberAdded(**_out(member, user).model_dump(), invited=invited)


@router.patch("/workspaces/{slug}/members/{user_id}", responses=_NOT_FOUND | _LAST_OWNER)
async def change_role(
    user_id: uuid.UUID, body: MemberUpdate, access: CanWrite, db: DbSession
) -> MemberOut:
    member, user = await crud.change_role(
        db, access.workspace_id, user_id=user_id, data=body, audit_ctx=access.audit
    )
    return _out(member, user)


@router.delete(
    "/workspaces/{slug}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND | _LAST_OWNER,
)
async def remove_member(user_id: uuid.UUID, access: CanWrite, db: DbSession) -> None:
    await crud.remove_member(db, access.workspace_id, user_id=user_id, audit_ctx=access.audit)


@router.delete(
    "/me/workspaces/{slug}",
    tags=["me"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(
        {404: "Workspace not found (or not a member)", 409: "last_owner: you are its last Owner"}
    ),
)
async def leave_workspace(slug: str, request: Request, user: CurrentUser, db: DbSession) -> None:
    """Leave a workspace I belong to (any role); its last active Owner can't."""
    found = await workspaces_crud.get_workspace_and_role(db, slug, user_id=user.id)
    if found is None or found[1] is None:
        raise NotFoundError(WORKSPACE_NOT_FOUND)
    await crud.leave(
        db,
        found[0],
        user_id=user.id,
        audit_ctx=request_audit(request, ActorType.USER, user),
    )
