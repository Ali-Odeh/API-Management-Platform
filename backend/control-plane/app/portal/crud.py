"""Database reads for the portal catalog (P.1).

Developers see only what workspaces publish: active workspaces, and in them
APIs that are published or deprecated. A draft, a suspended or deleted
workspace, and an unknown one all answer the same 404.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.apis.models import Api, ApiStatus
from app.core.errors import NotFoundError
from app.core.pagination import PageParams
from app.workspaces.crud import WORKSPACE_NOT_FOUND
from app.workspaces.models import Workspace, WorkspaceStatus

CATALOG_STATUSES = (ApiStatus.PUBLISHED, ApiStatus.DEPRECATED)


@dataclass(frozen=True)
class CatalogWorkspace:
    id: uuid.UUID
    slug: str
    name: str
    apis: int  # how many APIs it shows developers


async def list_catalog_workspaces(
    db: AsyncSession, *, page: PageParams
) -> tuple[list[CatalogWorkspace], int]:
    """Active workspaces that show developers at least one API, by name."""
    query = (
        select(Workspace.id, Workspace.slug, Workspace.name, func.count(Api.id).label("apis"))
        .join(Api, Api.workspace_id == Workspace.id)
        .where(Workspace.status == WorkspaceStatus.ACTIVE, Api.status.in_(CATALOG_STATUSES))
        .group_by(Workspace.id)
    )
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = await db.execute(
        query.order_by(Workspace.name, Workspace.id).offset(page.offset).limit(page.page_size)
    )
    return [CatalogWorkspace(*row) for row in rows.tuples()], total or 0


async def get_catalog_workspace_id(db: AsyncSession, slug: str) -> uuid.UUID:
    """The workspace behind a catalog URL; 404 unless it is active."""
    workspace_id = await db.scalar(
        select(Workspace.id).where(
            Workspace.slug == slug, Workspace.status == WorkspaceStatus.ACTIVE
        )
    )
    if workspace_id is None:
        raise NotFoundError(WORKSPACE_NOT_FOUND)
    return workspace_id
