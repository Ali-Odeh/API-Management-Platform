"""HTTP layer for users: the caller's own account and, for the Platform Admin, everyone's."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser, DbSession, GoTrue, PlatformAdmin, PlatformAudit
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.users import crud
from app.users.schemas import UserListItem, UserOut, UserUpdate

router = APIRouter(prefix="/api/v1")


@router.get("/me", tags=["me"])
async def me(user: CurrentUser) -> UserOut:
    """The signed-in person (their row is created on their first request)."""
    return UserOut.model_validate(user)


@router.get("/users", tags=["platform"])
async def list_users(
    admin: PlatformAdmin,
    db: DbSession,
    page: Pagination,
    is_active: Annotated[bool | None, Query(description="Only enabled or disabled people")] = None,
    email: Annotated[
        str | None, Query(max_length=255, description="Part of the email address, any case")
    ] = None,
) -> Page[UserListItem]:
    """Everyone with an account, newest first, with the workspaces they belong to."""
    users, total = await crud.list_users(db, page=page, is_active=is_active, email=email)
    workspaces = await crud.memberships_of(db, [u.id for u in users])
    return Page[UserListItem](
        items=[
            UserListItem(**UserOut.model_validate(u).model_dump(), workspaces=workspaces[u.id])
            for u in users
        ],
        total=total,
    )


@router.patch(
    "/users/{user_id}",
    tags=["platform"],
    responses=error_responses(
        {
            404: "User not found",
            409: "You can't disable yourself",
            502: "Auth service unavailable. An enable was not made; a disable was kept "
            "(the API is closed) and the ban is finished by repeating the request",
        }
    ),
)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    admin: PlatformAdmin,
    audit: PlatformAudit,
    db: DbSession,
    gotrue: GoTrue,
) -> UserOut:
    """Disable or enable a person: `{"is_active": false}` locks them out at once."""
    user = await crud.update_user(db, gotrue, actor=admin, user_id=user_id, data=body, audit=audit)
    return UserOut.model_validate(user)
