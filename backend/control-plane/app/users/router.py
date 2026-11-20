"""HTTP layer for users: the caller's own account and, for the Platform Admin, everyone's."""

import uuid

from fastapi import APIRouter

from app.core.deps import CurrentUser, DbSession, GoTrue, PlatformAdmin
from app.core.openapi import error_responses
from app.users import crud
from app.users.schemas import UserOut, UserUpdate

router = APIRouter(prefix="/api/v1")


@router.get("/me", tags=["me"])
async def me(user: CurrentUser) -> UserOut:
    """The signed-in person (their row is created on their first request)."""
    return UserOut.model_validate(user)


@router.patch(
    "/users/{user_id}",
    tags=["platform"],
    responses=error_responses(
        {
            404: "User not found",
            409: "You can't disable yourself",
            502: "Auth service unavailable; nothing was changed",
        }
    ),
)
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, admin: PlatformAdmin, db: DbSession, gotrue: GoTrue
) -> UserOut:
    """Disable or enable a person: `{"is_active": false}` locks them out at once."""
    user = await crud.update_user(db, gotrue, actor=admin, user_id=user_id, data=body)
    return UserOut.model_validate(user)
