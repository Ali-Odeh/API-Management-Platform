"""A workspace's limits and quotas (decision 6).

Each limit is the workspace's own value, or the platform default from the
settings when it has none (NULL). Quotas cap how many services, routes and
consumers a workspace may have: one tenant's config can't grow without end
and slow everyone's config builds.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.consumers.models import Consumer
from app.core.config import get_settings
from app.core.errors import ConflictError
from app.routes.models import Route
from app.services.models import Service
from app.workspaces.models import LIMIT_COLUMNS, Workspace

QUOTA_EXCEEDED = "quota_exceeded"

# The objects each quota counts, by its limit column.
COUNTED: dict[str, type[Service] | type[Route] | type[Consumer]] = {
    "max_services": Service,
    "max_routes": Route,
    "max_consumers": Consumer,
}


def default_limit(column: str) -> int:
    """The platform default for a limit column, e.g. WORKSPACE_MAX_SERVICES."""
    value: int = getattr(get_settings(), f"workspace_{column}")
    return value


def effective_limits(workspace: Workspace) -> dict[str, int]:
    """Every limit as it applies: the workspace's own value or the default."""
    return {
        column: own if (own := getattr(workspace, column)) is not None else default_limit(column)
        for column in LIMIT_COLUMNS
    }


async def count(db: AsyncSession, workspace_id: uuid.UUID, column: str) -> int:
    """How many objects of a quota's kind the workspace has."""
    model = COUNTED[column]
    return await db.scalar(select(func.count()).where(model.workspace_id == workspace_id)) or 0


async def check_quota(db: AsyncSession, workspace_id: uuid.UUID, column: str) -> None:
    """Refuse (409 quota_exceeded) when the workspace already has as many as it may.

    Locks the workspace row first (FOR NO KEY UPDATE, until the caller's
    commit), so parallel creations count one after the other: two requests
    can't both see "99 of 100" and both add one. The lock is compatible with
    the KEY SHARE locks taken by foreign-key checks.
    """
    own = await db.scalar(
        select(getattr(Workspace, column))
        .where(Workspace.id == workspace_id)
        .with_for_update(key_share=True)
    )
    allowed = own if own is not None else default_limit(column)
    if await count(db, workspace_id, column) >= allowed:
        raise ConflictError(QUOTA_EXCEEDED)  # the session's rollback releases the lock


async def usage(db: AsyncSession, workspace_id: uuid.UUID) -> dict[str, int]:
    """{"services": 3, "routes": 7, "consumers": 2}: what the quotas count now."""
    return {
        column.removeprefix("max_"): await count(db, workspace_id, column) for column in COUNTED
    }
