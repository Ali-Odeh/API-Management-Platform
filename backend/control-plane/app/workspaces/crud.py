from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.workspaces.models import DEFAULT_WORKSPACE_SLUG, Workspace


async def get_workspace_by_slug(db: AsyncSession, slug: str) -> Workspace | None:
    result = await db.scalars(select(Workspace).where(Workspace.slug == slug))
    return result.one_or_none()


async def get_or_create_default_workspace(db: AsyncSession) -> Workspace:
    """Return the `default` workspace, creating it on first run."""
    workspace = await get_workspace_by_slug(db, DEFAULT_WORKSPACE_SLUG)
    if workspace is None:
        workspace = Workspace(name="Default", slug=DEFAULT_WORKSPACE_SLUG)
        db.add(workspace)
        await db.flush()  # send the INSERT now so workspace.id is usable
    return workspace
