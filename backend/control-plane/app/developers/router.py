"""HTTP layer for developers, for the Platform Admin: list them, block or unblock one.

Open sign-up means anyone can become a developer; the Platform Admin is the
one who can stop an abusive one.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import DbSession, PlatformAdmin, PlatformAudit
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.developers import crud
from app.developers.models import DeveloperStatus
from app.developers.schemas import DeveloperOut, DeveloperUpdate

router = APIRouter(prefix="/api/v1/developers", tags=["platform"])


@router.get("")
async def list_developers(
    admin: PlatformAdmin,
    db: DbSession,
    page: Pagination,
    status: Annotated[DeveloperStatus | None, Query(description="Only active or blocked")] = None,
) -> Page[DeveloperOut]:
    """Everyone who signed up on the developer portal, newest first."""
    developers, total = await crud.list_developers(db, page=page, status=status)
    return Page[DeveloperOut](
        items=[DeveloperOut.model_validate(d) for d in developers], total=total
    )


@router.patch("/{developer_id}", responses=error_responses({404: "Developer not found"}))
async def update_developer(
    developer_id: uuid.UUID, body: DeveloperUpdate, audit: PlatformAudit, db: DbSession
) -> DeveloperOut:
    """Block or unblock a developer: `{"status": "blocked"}`."""
    developer = await crud.update_developer(db, developer_id=developer_id, data=body, audit=audit)
    return DeveloperOut.model_validate(developer)
