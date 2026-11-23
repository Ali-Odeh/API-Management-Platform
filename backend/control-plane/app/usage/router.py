"""HTTP layer for a workspace's analytics and request log (V2.1).

Every role may read them (`usage:read`). The rows come from the gateway
through the usage-ingest worker, usually a second or two after a request.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import DbSession, WorkspaceAccess, require_permission
from app.core.pagination import Page, Pagination
from app.core.permissions import Permission
from app.usage import crud
from app.usage.schemas import (
    ConsumerTraffic,
    Interval,
    Range,
    RequestLogOut,
    RouteTraffic,
    TopLimit,
    TopOut,
    TrafficOut,
)

router = APIRouter(prefix="/api/v1/workspaces/{slug}", tags=["usage"])

CanRead = Annotated[WorkspaceAccess, require_permission(Permission.USAGE_READ)]
RouteFilter = Annotated[uuid.UUID | None, Query(description="Only this route's requests")]
ConsumerFilter = Annotated[uuid.UUID | None, Query(description="Only this consumer's requests")]


@router.get("/analytics/traffic")
async def traffic(
    access: CanRead,
    db: DbSession,
    time: Range,
    interval: Interval = Interval.HOUR,
    route_id: RouteFilter = None,
    consumer_id: ConsumerFilter = None,
) -> TrafficOut:
    """Requests, client and server errors and latency over time (empty buckets as zero)."""
    return await crud.traffic(
        db,
        access.workspace_id,
        time=time,
        interval=interval,
        route_id=route_id,
        consumer_id=consumer_id,
    )


@router.get("/analytics/routes")
async def top_routes(
    access: CanRead, db: DbSession, time: Range, limit: TopLimit = 20
) -> TopOut[RouteTraffic]:
    """The busiest routes in the range."""
    items = await crud.top_routes(db, access.workspace_id, time=time, limit=limit)
    return TopOut[RouteTraffic](start=time.start, end=time.end, items=items)


@router.get("/analytics/consumers")
async def top_consumers(
    access: CanRead, db: DbSession, time: Range, limit: TopLimit = 20
) -> TopOut[ConsumerTraffic]:
    """The busiest consumers in the range."""
    items = await crud.top_consumers(db, access.workspace_id, time=time, limit=limit)
    return TopOut[ConsumerTraffic](start=time.start, end=time.end, items=items)


@router.get("/request-logs")
async def list_request_logs(
    access: CanRead,
    db: DbSession,
    time: Range,
    page: Pagination,
    route_id: RouteFilter = None,
    consumer_id: ConsumerFilter = None,
    status: Annotated[int | None, Query(ge=100, le=599, description="Only this status")] = None,
    method: Annotated[str | None, Query(max_length=10, description="Only this method")] = None,
) -> Page[RequestLogOut]:
    """The request log, newest first: one row per request the gateway answered."""
    rows, total = await crud.list_request_logs(
        db,
        access.workspace_id,
        time=time,
        page=page,
        route_id=route_id,
        consumer_id=consumer_id,
        status=status,
        method=method,
    )
    return Page[RequestLogOut](items=[RequestLogOut.model_validate(r) for r in rows], total=total)
