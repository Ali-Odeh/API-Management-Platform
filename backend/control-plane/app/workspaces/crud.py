import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.workspaces.models import DEFAULT_WORKSPACE_SLUG, Workspace


async def get_workspace_by_slug(db: AsyncSession, slug: str) -> Workspace | None:
    result = await db.scalars(select(Workspace).where(Workspace.slug == slug))
    return result.one_or_none()


async def get_default_workspace_id(db: AsyncSession) -> uuid.UUID:
    """The `default` workspace's id (created at startup)."""
    workspace_id = await db.scalar(
        select(Workspace.id).where(Workspace.slug == DEFAULT_WORKSPACE_SLUG)
    )
    if workspace_id is None:
        raise NotFoundError("Workspace not found")
    return workspace_id


async def get_or_create_default_workspace(db: AsyncSession) -> Workspace:
    """Return the `default` workspace, creating it on first run."""
    workspace = await get_workspace_by_slug(db, DEFAULT_WORKSPACE_SLUG)
    if workspace is None:
        workspace = Workspace(name="Default", slug=DEFAULT_WORKSPACE_SLUG)
        db.add(workspace)
        await db.flush()  # send the INSERT now so workspace.id is usable
    return workspace
