"""Reading the request log: analytics and the log itself (V2.1).

Every query is one workspace's rows in one time range, which the index on
(`workspace_id`, `ts`) serves, and which partition pruning keeps to the
months involved. Aggregates are computed by Postgres; nothing is loaded
row by row except one page of the log.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.consumers import crud as consumers_crud
from app.consumers.models import Consumer
from app.core.errors import NotFoundError, UnprocessableError
from app.core.pagination import PageParams, paginate
from app.routes import crud as routes_crud
from app.routes.models import Route
from app.usage.models import RequestLog
from app.usage.schemas import (
    MAX_BUCKETS,
    Bucket,
    ConsumerTraffic,
    Counts,
    Interval,
    Latency,
    RouteTraffic,
    TimeRange,
    TrafficOut,
)


async def check_filters(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    route_id: uuid.UUID | None,
    consumer_id: uuid.UUID | None,
) -> None:
    """A filter naming a route or consumer that isn't in this workspace is 422,
    like every filter: another workspace's object and an unknown one look the
    same. (So a deleted route or consumer can't be filtered on; its rows
    still show unfiltered.)"""
    try:
        if route_id is not None:
            await routes_crud.get_route(db, workspace_id, route_id=route_id)
        if consumer_id is not None:
            await consumers_crud.get_consumer(db, workspace_id, consumer_id=consumer_id)
    except NotFoundError as e:
        raise UnprocessableError(e.detail) from None


def _in_range(workspace_id: uuid.UUID, time: TimeRange) -> list[ColumnElement[bool]]:
    return [
        RequestLog.workspace_id == workspace_id,
        RequestLog.ts >= time.start,
        RequestLog.ts < time.end,
    ]


def _counts() -> list[Any]:
    """The aggregate columns every analytics answer has."""
    return [
        func.count().label("requests"),
        func.count().filter(RequestLog.status.between(400, 499)).label("client_errors"),
        func.count().filter(RequestLog.status >= 500).label("server_errors"),
        *(
            func.percentile_cont(p).within_group(RequestLog.latency_ms).label(f"p{name}")
            for p, name in ((0.5, "50"), (0.95, "95"), (0.99, "99"))
        ),
    ]


def _as_counts(row: Any) -> dict[str, Any]:
    def ms(value: float | None) -> float | None:
        return None if value is None else round(value, 3)

    return {
        "requests": row.requests,
        "client_errors": row.client_errors,
        "server_errors": row.server_errors,
        "latency": Latency(p50_ms=ms(row.p50), p95_ms=ms(row.p95), p99_ms=ms(row.p99)),
    }


def _bucket_starts(time: TimeRange, interval: Interval) -> list[datetime]:
    """Every bucket that overlaps the range, aligned to whole UTC units."""
    first = {
        Interval.MINUTE: time.start.replace(second=0, microsecond=0),
        Interval.HOUR: time.start.replace(minute=0, second=0, microsecond=0),
        Interval.DAY: time.start.replace(hour=0, minute=0, second=0, microsecond=0),
    }[interval]
    count = -(-(time.end - first) // interval.length)  # ceiling division
    if count > MAX_BUCKETS:
        raise UnprocessableError(f"More than {MAX_BUCKETS} buckets: choose a larger interval")
    return [first + i * interval.length for i in range(count)]


async def traffic(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    time: TimeRange,
    interval: Interval,
    route_id: uuid.UUID | None = None,
    consumer_id: uuid.UUID | None = None,
) -> TrafficOut:
    """Requests, errors and latency over time, in buckets of `interval`."""
    starts = _bucket_starts(time, interval)
    await check_filters(db, workspace_id, route_id=route_id, consumer_id=consumer_id)
    where = _in_range(workspace_id, time)
    if route_id is not None:
        where.append(RequestLog.route_id == route_id)
    if consumer_id is not None:
        where.append(RequestLog.consumer_id == consumer_id)

    bucket = func.date_trunc(interval.value, RequestLog.ts, "UTC").label("bucket")
    rows = await db.execute(select(bucket, *_counts()).where(*where).group_by(bucket))
    found = {row.bucket: _as_counts(row) for row in rows}
    totals = (await db.execute(select(*_counts()).where(*where))).one()
    empty = _as_counts_empty()
    return TrafficOut(
        start=time.start,
        end=time.end,
        interval=interval,
        totals=Counts(**_as_counts(totals)),
        buckets=[Bucket(start=s, **found.get(s, empty)) for s in starts],
    )


def _as_counts_empty() -> dict[str, Any]:
    return {
        "requests": 0,
        "client_errors": 0,
        "server_errors": 0,
        "latency": Latency(p50_ms=None, p95_ms=None, p99_ms=None),
    }


async def top_routes(
    db: AsyncSession, workspace_id: uuid.UUID, *, time: TimeRange, limit: int
) -> list[RouteTraffic]:
    """The busiest routes; requests that matched none are one entry (null)."""
    rows = (await _top(db, RequestLog.route_id, workspace_id, time, limit)).all()
    names = await _names(db, Route.path_prefix, Route, workspace_id, [r.key for r in rows])
    return [
        RouteTraffic(route_id=r.key, path_prefix=names.get(r.key), **_as_counts(r)) for r in rows
    ]


async def top_consumers(
    db: AsyncSession, workspace_id: uuid.UUID, *, time: TimeRange, limit: int
) -> list[ConsumerTraffic]:
    """The busiest consumers; requests without one (public routes, refused keys) are one entry."""
    rows = (await _top(db, RequestLog.consumer_id, workspace_id, time, limit)).all()
    names = await _names(db, Consumer.name, Consumer, workspace_id, [r.key for r in rows])
    return [
        ConsumerTraffic(consumer_id=r.key, name=names.get(r.key), **_as_counts(r)) for r in rows
    ]


async def _top(
    db: AsyncSession,
    key: Any,
    workspace_id: uuid.UUID,
    time: TimeRange,
    limit: int,
) -> Any:
    query = (
        select(key.label("key"), *_counts())
        .where(*_in_range(workspace_id, time))
        .group_by(key)
        .order_by(func.count().desc(), key)
        .limit(limit)
    )
    return await db.execute(query)


async def _names(
    db: AsyncSession,
    column: Any,
    model: type[Route] | type[Consumer],
    workspace_id: uuid.UUID,
    ids: Sequence[uuid.UUID | None],
) -> dict[uuid.UUID, str]:
    """Current names of the objects still there (in this workspace only)."""
    wanted = [i for i in ids if i is not None]
    if not wanted:
        return {}
    rows = await db.execute(
        select(model.id, column).where(model.workspace_id == workspace_id, model.id.in_(wanted))
    )
    return {row[0]: row[1] for row in rows}


async def list_request_logs(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    time: TimeRange,
    page: PageParams,
    route_id: uuid.UUID | None = None,
    consumer_id: uuid.UUID | None = None,
    status: int | None = None,
    method: str | None = None,
) -> tuple[list[RequestLog], int]:
    """One page of the request log, newest first."""
    await check_filters(db, workspace_id, route_id=route_id, consumer_id=consumer_id)
    query: Select[tuple[RequestLog]] = select(RequestLog).where(*_in_range(workspace_id, time))
    if route_id is not None:
        query = query.where(RequestLog.route_id == route_id)
    if consumer_id is not None:
        query = query.where(RequestLog.consumer_id == consumer_id)
    if status is not None:
        query = query.where(RequestLog.status == status)
    if method is not None:
        query = query.where(RequestLog.method == method.upper())
    return await paginate(db, query.order_by(RequestLog.ts.desc(), RequestLog.event_id), page)
