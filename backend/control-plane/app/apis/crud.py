"""All database work for APIs, always scoped to one workspace (P.1).

The rules (PLAN.md section 6, "APIs and the catalog"):
- the routes of an API are the workspace's own (422 otherwise), each in one
  API at most (409);
- the lifecycle goes draft -> published -> deprecated, and a deprecation can
  be undone (deprecated -> published); any other move is 409 api_<status>;
- only published and deprecated APIs reach developers.

Writes run with the workspace row locked (require_permission locks it for
every write), so two requests can't put one route into two APIs.
"""

import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.apis.models import PK_API_ROUTES, UQ_API_SLUG, Api, ApiRoute, ApiStatus
from app.apis.schemas import ApiCreate, ApiUpdate
from app.audit import crud as audit
from app.audit.changes import Changes, created, deleted, updated
from app.audit.context import AuditContext
from app.core.db import commit_or_raise
from app.core.errors import AppError, ConflictError, NotFoundError, UnprocessableError
from app.core.pagination import PageParams, paginate
from app.routes.models import Route

API_NOT_FOUND = "API not found"
ROUTE_NOT_FOUND = "Route not found"
# The moves the lifecycle allows; staying where it is changes nothing.
_TRANSITIONS = {
    (ApiStatus.DRAFT, ApiStatus.PUBLISHED),
    (ApiStatus.PUBLISHED, ApiStatus.DEPRECATED),
    (ApiStatus.DEPRECATED, ApiStatus.PUBLISHED),
}


def _errors(slug: str) -> dict[str, AppError]:
    return {
        UQ_API_SLUG: ConflictError(f"An API with the slug '{slug}' already exists"),
        PK_API_ROUTES: ConflictError("A route can be part of one API only"),
    }


# ---- Reading


async def list_apis(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    page: PageParams,
    statuses: Iterable[ApiStatus] = tuple(ApiStatus),
) -> tuple[list[Api], int]:
    """One page of APIs in the given statuses, by name."""
    query = (
        select(Api)
        .where(Api.workspace_id == workspace_id, Api.status.in_(list(statuses)))
        .order_by(Api.name, Api.id)
    )
    return await paginate(db, query, page)


async def get_api(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    api_id: uuid.UUID,
    statuses: Iterable[ApiStatus] = tuple(ApiStatus),
) -> Api:
    """The API, if it is in this workspace and in one of `statuses` (else 404)."""
    api = await db.scalar(
        select(Api).where(
            Api.id == api_id, Api.workspace_id == workspace_id, Api.status.in_(list(statuses))
        )
    )
    if api is None:
        raise NotFoundError(API_NOT_FOUND)
    return api


async def routes_of(
    db: AsyncSession, workspace_id: uuid.UUID, api_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[Route]]:
    """Each API's routes (by path), in one query for a whole page."""
    rows = await db.execute(
        select(ApiRoute.api_id, Route)
        .join(Route, (Route.id == ApiRoute.route_id) & (Route.workspace_id == workspace_id))
        .where(ApiRoute.workspace_id == workspace_id, ApiRoute.api_id.in_(api_ids))
        .order_by(Route.path_prefix)
    )
    found: dict[uuid.UUID, list[Route]] = defaultdict(list)
    for api_id, route in rows.tuples():
        found[api_id].append(route)
    return found


# ---- Writing


async def create_api(
    db: AsyncSession, workspace_id: uuid.UUID, *, data: ApiCreate, audit: AuditContext
) -> Api:
    api = Api(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name=data.name,
        slug=data.slug,
        description=data.description,
        status=ApiStatus.DRAFT,
        openapi=data.openapi,
    )
    db.add(api)
    await _set_routes(db, workspace_id, api, data.route_ids)
    after = _snapshot(api, data.route_ids)
    _record(db, audit, api, "api.create", created(after))
    await commit_or_raise(db, _errors(data.slug))
    await db.refresh(api)
    return api


async def update_api(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    api_id: uuid.UUID,
    data: ApiUpdate,
    audit: AuditContext,
) -> Api:
    api = await get_api(db, workspace_id, api_id=api_id)
    old_routes = await _route_ids(db, workspace_id, api.id)
    before = _snapshot(api, old_routes)
    sent: dict[str, Any] = data.model_dump(exclude_unset=True, exclude={"route_ids"})
    if "status" in sent:
        _check_transition(api.status, sent["status"])
    for field, value in sent.items():
        setattr(api, field, value)
    new_routes = old_routes
    if data.route_ids is not None and set(data.route_ids) != set(old_routes):
        await db.execute(delete(ApiRoute).where(ApiRoute.api_id == api.id))
        await _set_routes(db, workspace_id, api, data.route_ids)
        new_routes = data.route_ids
    if changes := updated(before, _snapshot(api, new_routes)):
        _record(db, audit, api, "api.update", changes)
    await commit_or_raise(db, _errors(api.slug))
    await db.refresh(api)
    return api


async def delete_api(
    db: AsyncSession, workspace_id: uuid.UUID, *, api_id: uuid.UUID, audit: AuditContext
) -> None:
    """Delete an API; its routes stay (only the links go, with the database's cascade)."""
    api = await get_api(db, workspace_id, api_id=api_id)
    routes = await _route_ids(db, workspace_id, api.id)
    _record(db, audit, api, "api.delete", deleted(_snapshot(api, routes)))
    await db.delete(api)
    await commit_or_raise(db, _errors(api.slug))


def _check_transition(current: ApiStatus, wanted: ApiStatus) -> None:
    """409 api_<status> for a move the lifecycle doesn't allow, naming where it is."""
    if current != wanted and (current, wanted) not in _TRANSITIONS:
        raise ConflictError(f"api_{current.value}")


async def _set_routes(
    db: AsyncSession, workspace_id: uuid.UUID, api: Api, route_ids: Sequence[uuid.UUID]
) -> None:
    """Link the routes to the API: each must be this workspace's (422) and in no
    other API (409, naming its path and that API)."""
    if not route_ids:
        return
    found = await db.execute(
        select(Route.id, Route.path_prefix, Api.name)
        .outerjoin(ApiRoute, ApiRoute.route_id == Route.id)
        .outerjoin(Api, Api.id == ApiRoute.api_id)
        .where(Route.workspace_id == workspace_id, Route.id.in_(route_ids))
    )
    rows = found.all()
    if len(rows) != len(route_ids):
        raise UnprocessableError(ROUTE_NOT_FOUND)
    for _, path_prefix, other_api in rows:
        if other_api is not None:
            raise ConflictError(f"The route {path_prefix} is already part of the API '{other_api}'")
    await db.flush()  # the API row first: the links point at it
    db.add_all(
        ApiRoute(route_id=route_id, api_id=api.id, workspace_id=workspace_id)
        for route_id in route_ids
    )


async def _route_ids(
    db: AsyncSession, workspace_id: uuid.UUID, api_id: uuid.UUID
) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(ApiRoute.route_id).where(
            ApiRoute.workspace_id == workspace_id, ApiRoute.api_id == api_id
        )
    )
    return list(rows)


def openapi_digest(document: dict[str, Any] | None) -> str | None:
    """What the audit log keeps of a document: a fingerprint, never the document
    itself (it can be a megabyte). A changed document changes the fingerprint."""
    if document is None:
        return None
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _snapshot(api: Api, route_ids: Iterable[uuid.UUID]) -> dict[str, Any]:
    return {
        "name": api.name,
        "slug": api.slug,
        "description": api.description,
        "status": api.status.value,
        "openapi": openapi_digest(api.openapi),
        "route_ids": sorted(str(r) for r in route_ids),
    }


def _record(
    db: AsyncSession, audit_ctx: AuditContext, api: Api, action: str, changes: Changes
) -> None:
    audit.record(
        db,
        audit_ctx,
        workspace_id=api.workspace_id,
        action=action,
        target_type="api",
        target_id=api.id,
        target_name=api.name,
        changes=changes,
    )
