"""The analytics and request-log endpoints (app/usage/router.py, V2.1).

Rows are written straight into `request_logs` as the owner (the way the
worker's inserts end up), so every number below is known exactly.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.permissions import Role
from app.workspaces.models import Workspace
from tests.conftest import OWNER_URL
from tests.factories import make_consumer, make_member, make_route, make_service, make_user
from tests.fakes import bearer, make_token

Headers = dict[str, str]
BASE = "/api/v1/workspaces/default"
# A fixed hour in the past, so buckets are exact.
T0 = (datetime.now(UTC) - timedelta(days=2)).replace(minute=0, second=0, microsecond=0)


async def log(workspace_id: uuid.UUID, *rows: dict[str, Any]) -> None:
    """Insert request log rows; each dict overrides a GET 200 of 10 ms at T0."""
    owner = create_async_engine(OWNER_URL, poolclass=pool.NullPool)
    try:
        async with owner.begin() as conn:
            months = sorted({r.get("ts", T0).date().replace(day=1) for r in rows})
            await conn.execute(
                text("SELECT ensure_request_log_partitions(CAST(:m AS date[]))"), {"m": months}
            )
            for r in rows:
                await conn.execute(
                    text(
                        "INSERT INTO request_logs (event_id, ts, workspace_id, request_id, method, "
                        "path, route_id, consumer_id, key_id, status, latency_ms, upstream_ms, "
                        "bytes, client_ip, ratelimit) VALUES (gen_random_uuid(), :ts, :ws, :rid, "
                        ":method, :path, :route, :consumer, :key, :status, :latency, 1, 10, "
                        ":ip, 'ok')"
                    ),
                    {
                        "ts": r.get("ts", T0),
                        "ws": workspace_id,
                        "rid": r.get("request_id"),
                        "method": r.get("method", "GET"),
                        "path": r.get("path", "/v1/orders"),
                        "route": r.get("route_id"),
                        "consumer": r.get("consumer_id"),
                        "key": r.get("key_id"),
                        "status": r.get("status", 200),
                        "latency": r.get("latency_ms", 10.0),
                        "ip": r.get("client_ip", "203.0.113.7"),
                    },
                )
    finally:
        await owner.dispose()


def window(hours: int = 3) -> dict[str, str]:
    return {"from": T0.isoformat(), "to": (T0 + timedelta(hours=hours)).isoformat()}


# ---- Traffic over time


async def test_traffic_counts_requests_errors_and_latency_per_bucket(
    client: AsyncClient, auth_headers: Headers, workspace: Workspace, other_workspace: Workspace
) -> None:
    await log(
        workspace.id,
        {"latency_ms": 10.0},
        {"latency_ms": 20.0, "status": 404},
        {"latency_ms": 30.0, "status": 503},
        {"ts": T0 + timedelta(hours=2, minutes=5), "latency_ms": 40.0},
    )
    await log(other_workspace.id, {"latency_ms": 999.0})  # never counted here

    r = await client.get(f"{BASE}/analytics/traffic", params=window(), headers=auth_headers)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["interval"] == "hour"
    counts = [(b["requests"], b["client_errors"], b["server_errors"]) for b in body["buckets"]]
    assert counts == [(3, 1, 1), (0, 0, 0), (1, 0, 0)]  # the empty hour is there too
    first = body["buckets"][0]
    assert datetime.fromisoformat(first["start"]) == T0
    assert first["latency"] == {"p50_ms": 20.0, "p95_ms": 29.0, "p99_ms": 29.8}
    assert body["buckets"][1]["latency"] == {"p50_ms": None, "p95_ms": None, "p99_ms": None}
    assert body["totals"]["requests"] == 4 and body["totals"]["latency"]["p50_ms"] == 25.0


async def test_traffic_by_minute_and_filtered(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    service = await make_service(db, workspace.id)
    route = await make_route(db, workspace.id, service.id)
    consumer = await make_consumer(db, workspace.id)
    await log(
        workspace.id,
        {"route_id": route.id, "consumer_id": consumer.id},
        {"ts": T0 + timedelta(minutes=1), "route_id": route.id},
        {"ts": T0 + timedelta(minutes=1)},  # another route
    )
    params = {"from": T0.isoformat(), "to": (T0 + timedelta(minutes=2)).isoformat()}

    by_route = await client.get(
        f"{BASE}/analytics/traffic",
        params={**params, "interval": "minute", "route_id": str(route.id)},
        headers=auth_headers,
    )
    by_consumer = await client.get(
        f"{BASE}/analytics/traffic",
        params={**params, "interval": "minute", "consumer_id": str(consumer.id)},
        headers=auth_headers,
    )

    assert [b["requests"] for b in by_route.json()["buckets"]] == [1, 1]
    assert [b["requests"] for b in by_consumer.json()["buckets"]] == [1, 0]


@pytest.mark.parametrize(
    ("params", "detail"),
    [
        ({"from": T0.isoformat(), "to": T0.isoformat()}, "`from` must be before `to`"),
        (
            {"from": T0.isoformat(), "to": (T0 + timedelta(days=32)).isoformat()},
            "At most 31 days at a time",
        ),
        (
            {
                "from": T0.isoformat(),
                "to": (T0 + timedelta(days=2)).isoformat(),
                "interval": "minute",
            },
            "More than 1500 buckets: choose a larger interval",
        ),
        ({"route_id": str(uuid.uuid4())}, "Route not found"),
        ({"consumer_id": str(uuid.uuid4())}, "Consumer not found"),
    ],
)
async def test_traffic_refuses_bad_ranges_and_unknown_filters(
    client: AsyncClient, auth_headers: Headers, params: dict[str, str], detail: str
) -> None:
    r = await client.get(f"{BASE}/analytics/traffic", params=params, headers=auth_headers)
    assert (r.status_code, r.json()) == (422, {"detail": detail})


async def test_the_range_defaults_to_the_last_day_and_reads_naive_times_as_utc(
    client: AsyncClient, auth_headers: Headers, workspace: Workspace
) -> None:
    await log(workspace.id, {"ts": datetime.now(UTC) - timedelta(minutes=5)})

    default = await client.get(f"{BASE}/analytics/traffic", headers=auth_headers)
    naive = await client.get(
        f"{BASE}/analytics/traffic",
        params={
            "from": T0.replace(tzinfo=None).isoformat(),
            "to": (T0 + timedelta(hours=1)).replace(tzinfo=None).isoformat(),
        },
        headers=auth_headers,
    )

    body = default.json()
    end, start = datetime.fromisoformat(body["end"]), datetime.fromisoformat(body["start"])
    assert end - start == timedelta(days=1)
    assert body["totals"]["requests"] == 1
    assert datetime.fromisoformat(naive.json()["start"]) == T0


# ---- The busiest routes and consumers


async def test_top_routes_and_consumers_busiest_first_with_their_names(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    service = await make_service(db, workspace.id)
    orders = await make_route(db, workspace.id, service.id, path_prefix="/v1/orders")
    users = await make_route(db, workspace.id, service.id, path_prefix="/v1/users")
    shop = await make_consumer(db, workspace.id, name="shop")
    gone = uuid.uuid4()  # a consumer deleted since
    await log(
        workspace.id,
        {"route_id": orders.id, "consumer_id": shop.id},
        {"route_id": orders.id, "consumer_id": shop.id},
        {"route_id": orders.id, "consumer_id": gone, "status": 500},
        {"route_id": users.id},
        {"status": 404},  # no route matched, no consumer
    )

    routes = await client.get(f"{BASE}/analytics/routes", params=window(), headers=auth_headers)
    consumers = await client.get(
        f"{BASE}/analytics/consumers", params={**window(), "limit": 2}, headers=auth_headers
    )

    # Ties by id, where null comes last.
    assert [
        (i["path_prefix"], i["requests"], i["server_errors"]) for i in routes.json()["items"]
    ] == [("/v1/orders", 3, 1), ("/v1/users", 1, 0), (None, 1, 0)]
    items = consumers.json()["items"]
    assert [(i["name"], i["requests"]) for i in items] == [("shop", 2), (None, 2)]
    assert len(items) == 2  # the limit


# ---- The request log


async def test_the_request_log_newest_first_paged_and_filtered(
    client: AsyncClient, auth_headers: Headers, workspace: Workspace, other_workspace: Workspace
) -> None:
    await log(
        workspace.id,
        {"ts": T0, "request_id": "first"},
        {"ts": T0 + timedelta(minutes=1), "request_id": "second", "status": 429},
        {"ts": T0 + timedelta(minutes=2), "request_id": "third", "method": "POST"},
    )
    await log(other_workspace.id, {"ts": T0, "request_id": "theirs"})

    page = await client.get(
        f"{BASE}/request-logs", params={**window(), "page_size": 2}, headers=auth_headers
    )
    limited = await client.get(
        f"{BASE}/request-logs", params={**window(), "status": 429}, headers=auth_headers
    )
    posts = await client.get(
        f"{BASE}/request-logs", params={**window(), "method": "post"}, headers=auth_headers
    )

    assert page.status_code == 200, page.text
    assert page.json()["total"] == 3
    assert [i["request_id"] for i in page.json()["items"]] == ["third", "second"]
    first = page.json()["items"][1]
    assert (first["status"], first["client_ip"], first["ratelimit"]) == (429, "203.0.113.7", "ok")
    assert [i["request_id"] for i in limited.json()["items"]] == ["second"]
    assert [i["request_id"] for i in posts.json()["items"]] == ["third"]


async def test_a_viewer_reads_usage_too(
    client: AsyncClient, db: AsyncSession, workspace: Workspace
) -> None:
    viewer = await make_user(db, email="viewer@test.local")
    await make_member(db, workspace.id, viewer.id, Role.VIEWER)
    assert viewer.auth_user_id is not None
    headers = bearer(make_token(viewer.auth_user_id, viewer.email))

    for path in ("analytics/traffic", "analytics/routes", "analytics/consumers", "request-logs"):
        r = await client.get(f"{BASE}/{path}", headers=headers)
        assert r.status_code == 200, (path, r.text)


async def test_the_request_log_filtered_by_route_and_consumer(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    service = await make_service(db, workspace.id)
    route = await make_route(db, workspace.id, service.id)
    consumer = await make_consumer(db, workspace.id)
    await log(
        workspace.id,
        {"request_id": "both", "route_id": route.id, "consumer_id": consumer.id},
        {"request_id": "route-only", "route_id": route.id},
        {"request_id": "neither"},
    )

    by_route = await client.get(
        f"{BASE}/request-logs", params={**window(), "route_id": str(route.id)}, headers=auth_headers
    )
    by_consumer = await client.get(
        f"{BASE}/request-logs",
        params={**window(), "consumer_id": str(consumer.id)},
        headers=auth_headers,
    )

    assert sorted(i["request_id"] for i in by_route.json()["items"]) == ["both", "route-only"]
    assert [i["request_id"] for i in by_consumer.json()["items"]] == ["both"]
