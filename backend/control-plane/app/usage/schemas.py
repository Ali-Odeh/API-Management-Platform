"""Shapes of the analytics and request-log endpoints (V2.1)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Generic, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, IPvAnyAddress

from app.core.errors import UnprocessableError

MAX_RANGE = timedelta(days=31)
DEFAULT_RANGE = timedelta(days=1)
MAX_BUCKETS = 1500  # e.g. a day by the minute, or 31 days by the hour
MAX_TOP = 100


class Interval(StrEnum):
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"

    @property
    def length(self) -> timedelta:
        return {
            Interval.MINUTE: timedelta(minutes=1),
            Interval.HOUR: timedelta(hours=1),
            Interval.DAY: timedelta(days=1),
        }[self]


@dataclass(frozen=True)
class TimeRange:
    """[start, end): at most MAX_RANGE long; the last day by default."""

    start: datetime
    end: datetime


def _utc(value: datetime) -> datetime:
    """A time without a zone is read as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _time_range(
    since: Annotated[
        datetime | None,
        Query(alias="from", description="At or after this time (default: a day before `to`)"),
    ] = None,
    until: Annotated[
        datetime | None, Query(alias="to", description="Before this time (default: now)")
    ] = None,
) -> TimeRange:
    end = _utc(until) if until is not None else datetime.now(UTC)
    start = _utc(since) if since is not None else end - DEFAULT_RANGE
    if start >= end:
        raise UnprocessableError("`from` must be before `to`")
    if end - start > MAX_RANGE:
        raise UnprocessableError(f"At most {MAX_RANGE.days} days at a time")
    return TimeRange(start=start, end=end)


Range = Annotated[TimeRange, Depends(_time_range)]


class Latency(BaseModel):
    """Percentiles of the whole request's time at the gateway (null: no requests)."""

    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None


class Counts(BaseModel):
    requests: int
    client_errors: int  # 4xx: refused keys, limits, unknown routes, ...
    server_errors: int  # 5xx: backends down or slow, no healthy target, ...
    latency: Latency


class Bucket(Counts):
    start: datetime


class TrafficOut(BaseModel):
    """Requests over time; empty buckets are included (zero)."""

    start: datetime
    end: datetime
    interval: Interval
    totals: Counts
    buckets: list[Bucket]


class RouteTraffic(Counts):
    route_id: uuid.UUID | None  # null: no route matched (404, 405)
    path_prefix: str | None  # null: no route, or deleted since


class ConsumerTraffic(Counts):
    consumer_id: uuid.UUID | None  # null: public routes and refused keys
    name: str | None  # null: no consumer, or deleted since


T = TypeVar("T")


class TopOut(BaseModel, Generic[T]):
    start: datetime
    end: datetime
    items: list[T]


TopLimit = Annotated[int, Query(ge=1, le=MAX_TOP, description="How many, busiest first")]


class RequestLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: uuid.UUID
    ts: datetime
    request_id: str | None
    method: str
    path: str
    status: int
    latency_ms: float
    upstream_ms: float
    bytes: int
    route_id: uuid.UUID | None
    target_id: uuid.UUID | None
    consumer_id: uuid.UUID | None
    key_id: uuid.UUID | None
    client_ip: IPvAnyAddress | None
    ratelimit: str | None
