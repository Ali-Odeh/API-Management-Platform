"""The developer portal API: what a signed-in developer sees (P.1). No SQL here.

Only the developers' tokens open it (see portal/deps.py). The catalog
shows the published and deprecated APIs of active workspaces; anything else
(a draft, another workspace's draft, a suspended workspace) is 404, the
same answer as for something that doesn't exist.
"""

import uuid

from fastapi import APIRouter

from app.apis import crud as apis_crud
from app.apis.models import Api
from app.core.deps import DbSession
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.developers.schemas import DeveloperOut
from app.plans import crud as plans_crud
from app.portal import crud
from app.portal.deps import CurrentDeveloper
from app.portal.schemas import (
    CatalogApi,
    CatalogApiSummary,
    CatalogRoute,
    CatalogWorkspaceOut,
    OfferedPlan,
)
from app.routes.models import Route

router = APIRouter(prefix="/api/v1/portal", tags=["portal"])

_WORKSPACE_NOT_FOUND = error_responses({404: "Workspace not found"})


def _summary(api: Api, routes: list[Route]) -> CatalogApiSummary:
    return CatalogApiSummary(
        id=api.id,
        name=api.name,
        slug=api.slug,
        description=api.description,
        status=api.status,
        has_openapi=api.openapi is not None,
        routes=[CatalogRoute.model_validate(r) for r in routes],
        updated_at=api.updated_at,
    )


@router.get("/me")
async def me(caller: CurrentDeveloper) -> DeveloperOut:
    """The signed-in developer (their row is made on their first request)."""
    return DeveloperOut.model_validate(caller.developer)


@router.get("/workspaces")
async def list_workspaces(
    caller: CurrentDeveloper, db: DbSession, page: Pagination
) -> Page[CatalogWorkspaceOut]:
    """Workspaces that publish APIs, by name, with how many."""
    workspaces, total = await crud.list_catalog_workspaces(db, page=page)
    return Page[CatalogWorkspaceOut](
        items=[CatalogWorkspaceOut.model_validate(w) for w in workspaces], total=total
    )


@router.get("/workspaces/{slug}/apis", responses=_WORKSPACE_NOT_FOUND)
async def list_apis(
    slug: str, caller: CurrentDeveloper, db: DbSession, page: Pagination
) -> Page[CatalogApiSummary]:
    """A workspace's catalog: its published and deprecated APIs, by name."""
    workspace_id = await crud.get_catalog_workspace_id(db, slug)
    apis, total = await apis_crud.list_apis(
        db, workspace_id, page=page, statuses=crud.CATALOG_STATUSES
    )
    routes = await apis_crud.routes_of(db, workspace_id, [a.id for a in apis])
    return Page[CatalogApiSummary](
        items=[_summary(a, routes.get(a.id, [])) for a in apis], total=total
    )


@router.get(
    "/workspaces/{slug}/apis/{api_id}",
    responses=error_responses({404: "Workspace or API not found (drafts too)"}),
)
async def get_api(
    slug: str, api_id: uuid.UUID, caller: CurrentDeveloper, db: DbSession
) -> CatalogApi:
    """One API of the catalog, with its OpenAPI document."""
    workspace_id = await crud.get_catalog_workspace_id(db, slug)
    api = await apis_crud.get_api(db, workspace_id, api_id=api_id, statuses=crud.CATALOG_STATUSES)
    routes = await apis_crud.routes_of(db, workspace_id, [api.id])
    return CatalogApi(**_summary(api, routes.get(api.id, [])).model_dump(), openapi=api.openapi)


@router.get("/workspaces/{slug}/plans", responses=_WORKSPACE_NOT_FOUND)
async def list_plans(
    slug: str, caller: CurrentDeveloper, db: DbSession, page: Pagination
) -> Page[OfferedPlan]:
    """The plans a developer may choose for an app in this workspace (P.2)."""
    workspace_id = await crud.get_catalog_workspace_id(db, slug)
    plans, total = await plans_crud.list_offered_plans(db, workspace_id, page=page)
    return Page[OfferedPlan](items=[OfferedPlan.model_validate(p) for p in plans], total=total)
