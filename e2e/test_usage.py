"""V2.1 stories: every request through the gateway is in its workspace's request log.

Two fresh workspaces (new slugs every run) publish /v1/orders. Known
traffic goes through the gateway, each request with its own X-Request-ID;
the usage-ingest worker writes it to the request log within a second or
two. Each workspace finds exactly its own requests, in the log and in the
analytics, and nothing of the other's. The workspaces end deleted.
"""

import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from conftest import MOCK_BACKEND_URL, PERSON_EMAIL, Session, wait_for

INGEST_WAIT = 15.0  # seconds; the gateway flushes every second, the worker reads at once


@dataclass
class Tenant:
    slug: str
    id: str
    key: str
    key_id: str
    consumer_id: str
    route_id: str

    @property
    def host(self) -> str:
        return f"{self.slug}.localhost"

    @property
    def base(self) -> str:
        return f"/workspaces/{self.slug}"


def _make_tenant(admin: httpx.Client, slug: str) -> Tenant:
    r = admin.post("/workspaces", json={"name": slug, "slug": slug, "owner_email": PERSON_EMAIL})
    assert r.status_code == 201, r.text
    workspace_id = r.json()["id"]
    base = f"/workspaces/{slug}"
    service = admin.post(
        f"{base}/services", json={"name": "orders", "targets": [{"url": MOCK_BACKEND_URL}]}
    ).json()
    route = admin.post(
        f"{base}/routes",
        json={"service_id": service["id"], "path_prefix": "/v1/orders", "methods": ["GET"]},
    ).json()
    consumer = admin.post(
        f"{base}/consumers", json={"name": "app", "email": "e2e@example.com"}
    ).json()
    key = admin.post(f"{base}/consumers/{consumer['id']}/keys", json={"name": "e2e"}).json()
    return Tenant(slug, workspace_id, key["key"], key["id"], consumer["id"], route["id"])


@pytest.fixture
def tenants(admin: httpx.Client, person: Session) -> Iterator[tuple[Tenant, Tenant]]:
    run = uuid.uuid4().hex[:6]
    made: list[Tenant] = []
    try:
        for side in ("a", "b"):
            made.append(_make_tenant(admin, f"e2e-usage-{side}-{run}"))
        yield made[0], made[1]
    finally:
        for tenant in made:
            r = admin.request("DELETE", tenant.base, json={"confirm": tenant.slug})
            assert r.status_code == 204, r.text


def _send(gateway: httpx.Client, tenant: Tenant, request_id: str, key: str) -> int:
    r = gateway.get(
        "/v1/orders/1", headers={"Host": tenant.host, "X-API-Key": key, "X-Request-ID": request_id}
    )
    return r.status_code


def _logged(admin: httpx.Client, tenant: Tenant, since: datetime, want: int) -> dict[str, Any]:
    """The tenant's request log since `since`, once it holds `want` rows."""
    params = {
        "from": since.isoformat(),
        "to": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        "page_size": 100,
    }
    deadline = time.monotonic() + INGEST_WAIT
    while True:
        page: dict[str, Any] = admin.get(f"{tenant.base}/request-logs", params=params).json()
        if page["total"] >= want or time.monotonic() > deadline:
            return page
        time.sleep(0.2)


def test_known_traffic_is_in_each_workspaces_log_and_analytics_only(
    gateway: httpx.Client, admin: httpx.Client, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    run = uuid.uuid4().hex[:8]
    assert (
        wait_for(
            lambda: gateway.get("/v1/orders/1", headers={"Host": a.host, "X-API-Key": a.key}), 200
        ).status_code
        == 200
    )
    assert (
        wait_for(
            lambda: gateway.get("/v1/orders/1", headers={"Host": b.host, "X-API-Key": b.key}), 200
        ).status_code
        == 200
    )
    since = datetime.now(UTC)

    sent_a = [f"{run}-a-ok-{i}" for i in range(5)]
    sent_a_bad = [f"{run}-a-bad-{i}" for i in range(2)]
    sent_b = [f"{run}-b-ok-{i}" for i in range(3)]
    assert {_send(gateway, a, rid, a.key) for rid in sent_a} == {200}
    assert {_send(gateway, a, rid, "ak_live_wrong") for rid in sent_a_bad} == {401}
    assert {_send(gateway, b, rid, b.key) for rid in sent_b} == {200}

    log_a = _logged(admin, a, since, 7)
    log_b = _logged(admin, b, since, 3)

    # Exactly the requests sent, each once, newest first.
    assert sorted(i["request_id"] for i in log_a["items"]) == sorted(sent_a + sent_a_bad)
    assert sorted(i["request_id"] for i in log_b["items"]) == sorted(sent_b)
    ok = next(i for i in log_a["items"] if i["request_id"] == sent_a[0])
    assert (ok["status"], ok["route_id"], ok["consumer_id"], ok["key_id"]) == (
        200,
        a.route_id,
        a.consumer_id,
        a.key_id,
    )
    bad = next(i for i in log_a["items"] if i["request_id"] == sent_a_bad[0])
    assert (bad["status"], bad["consumer_id"]) == (401, None)

    # The analytics count the same requests.
    window = {
        "from": since.isoformat(),
        "to": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    }
    traffic = admin.get(
        f"{a.base}/analytics/traffic", params={**window, "interval": "minute"}
    ).json()
    assert (traffic["totals"]["requests"], traffic["totals"]["client_errors"]) == (7, 2)
    assert sum(bucket["requests"] for bucket in traffic["buckets"]) == 7
    consumers = admin.get(f"{a.base}/analytics/consumers", params=window).json()["items"]
    assert {(c["consumer_id"], c["requests"]) for c in consumers} == {(a.consumer_id, 5), (None, 2)}
    assert admin.get(f"{b.base}/analytics/traffic", params=window).json()["totals"]["requests"] == 3

    # Its key's last use is known now.
    keys = admin.get(f"{a.base}/consumers/{a.consumer_id}/keys").json()["items"]
    last_used = datetime.fromisoformat(keys[0]["last_used_at"])
    assert since - timedelta(minutes=2) <= last_used <= datetime.now(UTC)


def test_filtering_the_log_by_another_workspaces_route_is_refused(
    admin: httpx.Client, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    r = admin.get(f"{a.base}/request-logs", params={"route_id": b.route_id})
    assert (r.status_code, r.json()) == (422, {"detail": "Route not found"})
