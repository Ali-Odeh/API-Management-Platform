"""HTTP layer for a workspace's APIs (members, by permission). No SQL here."""

import uuid
from typing import Annotated

from fastapi import APIRouter, status

from app.apis import crud
from app.apis.models import Api
from app.apis.schemas import ApiCreate, ApiOut, ApiRouteOut, ApiSummary, ApiUpdate
from app.core.deps import DbSession, WorkspaceAccess, require_permission
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.core.permissions import Permission
from app.routes.models import Route

router = APIRouter(prefix="/api/v1/workspaces/{slug}/apis", tags=["apis"])

CanRead = Annotated[WorkspaceAccess, require_permission(Permission.APIS_READ)]
CanWrite = Annotated[WorkspaceAccess, require_permission(Permission.APIS_WRITE)]

_NOT_FOUND = error_responses({404: "API not found"})
_WRITE = error_responses(
    {
        409: "Slug already used in this workspace, a route already in another API, "
        "or a move the lifecycle doesn't allow (api_draft, api_published, api_deprecated)",
        422: "Invalid input, or a route that isn't in this workspace",
    }
)


def api_summary(api: Api, routes: list[Route]) -> ApiSummary:
    return ApiSummary(
        id=api.id,
        name=api.name,
        slug=api.slug,
        description=api.description,
        status=api.status,
        has_openapi=api.openapi is not None,
        routes=[ApiRouteOut.model_validate(r) for r in routes],
        created_at=api.created_at,
        updated_at=api.updated_at,
    )


def api_out(api: Api, routes: list[Route]) -> ApiOut:
    return ApiOut(**api_summary(api, routes).model_dump(), openapi=api.openapi)


async def _out(db: DbSession, access: WorkspaceAccess, api: Api) -> ApiOut:
    routes = await crud.routes_of(db, access.workspace_id, [api.id])
    return api_out(api, routes.get(api.id, []))


@router.get("")
async def list_apis(access: CanRead, db: DbSession, page: Pagination) -> Page[ApiSummary]:
    """The workspace's APIs in every status, by name, without their documents."""
    apis, total = await crud.list_apis(db, access.workspace_id, page=page)
    routes = await crud.routes_of(db, access.workspace_id, [a.id for a in apis])
    return Page[ApiSummary](items=[api_summary(a, routes.get(a.id, [])) for a in apis], total=total)


@router.post("", status_code=status.HTTP_201_CREATED, responses=_WRITE)
async def create_api(body: ApiCreate, access: CanWrite, db: DbSession) -> ApiOut:
    """A new API, as a draft: publish it with `PATCH {"status": "published"}`."""
    api = await crud.create_api(db, access.workspace_id, data=body, audit=access.audit)
    return await _out(db, access, api)


@router.get("/{api_id}", responses=_NOT_FOUND)
async def get_api(api_id: uuid.UUID, access: CanRead, db: DbSession) -> ApiOut:
    api = await crud.get_api(db, access.workspace_id, api_id=api_id)
    return await _out(db, access, api)


@router.patch("/{api_id}", responses=_NOT_FOUND | _WRITE)
async def update_api(api_id: uuid.UUID, body: ApiUpdate, access: CanWrite, db: DbSession) -> ApiOut:
    api = await crud.update_api(
        db, access.workspace_id, api_id=api_id, data=body, audit=access.audit
    )
    return await _out(db, access, api)


@router.delete("/{api_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
async def delete_api(api_id: uuid.UUID, access: CanWrite, db: DbSession) -> None:
    """Delete an API. Its routes stay and keep working at the gateway."""
    await crud.delete_api(db, access.workspace_id, api_id=api_id, audit=access.audit)
