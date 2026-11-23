"""V2.3: the v2 definition of done (PLAN.md section 1), as one setup.

An Owner creates Free (60/min, a small monthly quota for the test) and Pro
(600/min, no quota) and puts one consumer on each. Both call through the
gateway: Free gets 429 quota_exceeded after its quota, Pro keeps working; a
plan above the workspace's limit is 422. Every request is in the request
log within seconds, the analytics count them, each key's last_used_at
moves, and Prometheus has scraped the gateway's counters for the
workspace. (Redis stopped is covered by the gateway's tests: the e2e
container can't stop it.) The workspace is new each run and ends deleted.
"""

import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from conftest import MOCK_BACKEND_URL, PERSON_EMAIL, Session, wait_for

PROMETHEUS_URL = "http://prometheus:9090"
FREE_QUOTA = 5
WAIT = 20.0  # seconds: the worker stores within a second or two, Prometheus scrapes every 5


@pytest.fixture
def shop(admin: httpx.Client, person: Session) -> Iterator[dict[str, str]]:
    slug = f"e2e-v2-{uuid.uuid4().hex[:6]}"
    r = admin.post("/workspaces", json={"name": slug, "slug": slug, "owner_email": PERSON_EMAIL})
    assert r.status_code == 201, r.text
    base = f"/workspaces/{slug}"
    try:
        service = admin.post(
            f"{base}/services", json={"name": "api", "targets": [{"url": MOCK_BACKEND_URL}]}
        ).json()
        admin.post(
            f"{base}/routes",
            json={"service_id": service["id"], "path_prefix": "/orders", "methods": ["GET"]},
        )
        yield {"slug": slug, "id": r.json()["id"], "base": base, "host": f"{slug}.localhost"}
    finally:
        r = admin.request("DELETE", base, json={"confirm": slug})
        assert r.status_code == 204, r.text


def _until(what: str, check: Any) -> Any:
    deadline = time.monotonic() + WAIT
    while True:
        if result := check():
            return result
        if time.monotonic() > deadline:
            pytest.fail(f"not within {WAIT:.0f} s: {what}")
        time.sleep(0.5)


def test_the_v2_definition_of_done(
    gateway: httpx.Client, admin: httpx.Client, shop: dict[str, str]
) -> None:
    base, host = shop["base"], shop["host"]
    since = datetime.now(UTC) - timedelta(seconds=5)

    # Plans, and a plan above the workspace's own limit refused.
    free = admin.post(
        f"{base}/plans",
        json={"name": "Free", "rate_limit_per_min": 60, "monthly_quota": FREE_QUOTA},
    ).json()
    pro = admin.post(f"{base}/plans", json={"name": "Pro", "rate_limit_per_min": 600}).json()
    ceiling = admin.get(base).json()["limits"]["rate_limit_per_min"]
    assert (
        admin.post(
            f"{base}/plans", json={"name": "X", "rate_limit_per_min": ceiling + 1}
        ).status_code
        == 422
    )

    keys = {}
    for name, plan in (("free-app", free), ("pro-app", pro)):
        consumer = admin.post(
            f"{base}/consumers",
            json={"name": name, "email": "e2e@example.com", "plan_id": plan["id"]},
        ).json()
        key = admin.post(f"{base}/consumers/{consumer['id']}/keys", json={"name": "k"}).json()
        keys[name] = (consumer["id"], key["key"])

    def order(name: str) -> httpx.Response:
        return gateway.get("/orders/1", headers={"Host": host, "X-API-Key": keys[name][1]})

    # Free runs out of its monthly quota; Pro keeps working.
    assert wait_for(lambda: order("free-app"), 200).status_code == 200
    statuses = [order("free-app").status_code for _ in range(FREE_QUOTA)]
    assert statuses == [200] * (FREE_QUOTA - 1) + [429]
    assert order("free-app").json() == {"error": "quota_exceeded"}
    assert wait_for(lambda: order("pro-app"), 200).status_code == 200
    sent = 1 + FREE_QUOTA + 1 + 1  # free: first, the rest, the refusal; pro: one

    # Every request is in the request log, and the analytics count them.
    window = {
        "from": since.isoformat(),
        "to": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    }

    def logged() -> dict[str, Any] | None:
        page: dict[str, Any] = admin.get(
            f"{base}/request-logs", params={**window, "page_size": 100}
        ).json()
        return page if page["total"] >= sent else None

    log = _until("every request in the log", logged)
    assert sum(1 for i in log["items"] if i["status"] == 429) >= 2
    traffic = admin.get(f"{base}/analytics/traffic", params=window).json()["totals"]
    assert traffic["requests"] == log["total"] and traffic["latency"]["p95_ms"] is not None
    by_consumer = admin.get(f"{base}/analytics/consumers", params=window).json()["items"]
    assert {c["name"] for c in by_consumer} >= {"free-app", "pro-app"}

    # Each key's last use is known.
    for consumer_id, _ in keys.values():
        [key] = admin.get(f"{base}/consumers/{consumer_id}/keys").json()["items"]
        assert key["last_used_at"] is not None

    # Prometheus has scraped the gateway's counters for this workspace.
    query = f'sum(gateway_requests_total{{workspace="{shop["id"]}"}})'

    def scraped() -> bool:
        result = httpx.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}).json()
        found = result["data"]["result"]
        return bool(found) and float(found[0]["value"][1]) >= sent

    assert _until("the gateway's requests in Prometheus", scraped)
