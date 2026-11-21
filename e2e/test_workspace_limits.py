"""MT.8 stories: one workspace can't take the gateway from the others.

Each story makes its own workspace (new slugs: slugs are never reused) with a
public and a protected route to the mock backend, lowers its limits through
the Platform Admin's `PATCH /limits`, and calls the gateway with
`Host: <slug>.localhost`. The limits are set before the routes exist, so the
gateway never serves these routes under the default limits. The workspaces
are deleted at the end (the purge job removes their data later).
"""

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx
import pytest

from conftest import MOCK_BACKEND_URL, PERSON_EMAIL, Session, wait_for


@dataclass
class Limited:
    slug: str
    key: str  # of a consumer whose own limit is far above the workspace's

    @property
    def host(self) -> str:
        return f"{self.slug}.localhost"


def _make(admin: httpx.Client, rate_limit_per_min: int, max_concurrent: int) -> Limited:
    slug = f"e2e-lim-{uuid.uuid4().hex[:6]}"
    r = admin.post("/workspaces", json={"name": slug, "slug": slug, "owner_email": PERSON_EMAIL})
    assert r.status_code == 201, r.text
    base = f"/workspaces/{slug}"
    r = admin.patch(
        f"{base}/limits",
        json={"rate_limit_per_min": rate_limit_per_min, "max_concurrent": max_concurrent},
    )
    assert r.status_code == 200, r.text
    r = admin.post(f"{base}/services", json={"name": "api", "upstream_url": MOCK_BACKEND_URL})
    assert r.status_code == 201, r.text
    service_id = r.json()["id"]
    for prefix, auth_required in (("/public", False), ("/private", True)):
        r = admin.post(
            f"{base}/routes",
            json={
                "service_id": service_id,
                "path_prefix": prefix,
                "methods": ["GET"],
                "auth_required": auth_required,
            },
        )
        assert r.status_code == 201, r.text
    r = admin.post(
        f"{base}/consumers",
        json={"name": "app", "email": "e2e@example.com", "rate_limit_per_min": 1000},
    )
    assert r.status_code == 201, r.text
    r = admin.post(f"{base}/consumers/{r.json()['id']}/keys", json={"name": "e2e"})
    assert r.status_code == 201, r.text
    return Limited(slug=slug, key=r.json()["key"])


@pytest.fixture
def limited(admin: httpx.Client, person: Session) -> Iterator[Callable[[int, int], Limited]]:
    """Makes workspaces with the given limits, owned by the test person."""
    made: list[Limited] = []

    def make(rate_limit_per_min: int, max_concurrent: int) -> Limited:
        made.append(_make(admin, rate_limit_per_min, max_concurrent))
        return made[-1]

    try:
        yield make
    finally:
        for ws in made:
            r = admin.request("DELETE", f"/workspaces/{ws.slug}", json={"confirm": ws.slug})
            assert r.status_code == 204, r.text


def _get(gateway: httpx.Client, ws: Limited, path: str, key: str | None = None) -> httpx.Response:
    headers = {"Host": ws.host}
    if key:
        headers["X-API-Key"] = key
    return gateway.get(path, headers=headers)


def test_a_workspace_over_its_rate_limit_is_429_on_every_route(
    gateway: httpx.Client, limited: Callable[[int, int], Limited]
) -> None:
    ws = limited(3, 200)  # 3 requests per minute for the whole workspace

    # Requests refused before the rate limit (route not served yet) cost
    # nothing, so the first 200 is request 1 of 3. Public routes, unlimited
    # before MT.8, now count against the workspace.
    first = wait_for(lambda: _get(gateway, ws, "/public/x"), 200)
    statuses = [first.status_code] + [_get(gateway, ws, "/public/x").status_code for _ in range(3)]
    assert statuses == [200, 200, 200, 429]
    assert first.headers["X-RateLimit-Limit"] == "3"

    r = _get(gateway, ws, "/public/x")
    assert r.json() == {"error": "rate_limit_exceeded"}
    assert (r.headers["X-RateLimit-Limit"], r.headers["X-RateLimit-Remaining"]) == ("3", "0")
    assert int(r.headers["Retry-After"]) >= 1

    # A consumer with plenty of its own budget is refused too: the
    # workspace's bucket is empty, and the answer describes it.
    r = wait_for(lambda: _get(gateway, ws, "/private/x", ws.key), 429)
    assert r.json() == {"error": "rate_limit_exceeded"}
    assert r.headers["X-RateLimit-Limit"] == "3"


def test_a_workspace_with_its_requests_in_flight_is_503_until_one_ends(
    gateway: httpx.Client, limited: Callable[[int, int], Limited]
) -> None:
    ws = limited(6000, 1)  # one request in flight at a time
    other = limited(6000, 200)
    for w in (ws, other):
        assert wait_for(lambda w=w: _get(gateway, w, "/public/x"), 200).status_code == 200

    # One slow request (the mock backend answers 2 s late) holds the only slot.
    slow: list[httpx.Response] = []
    holder = threading.Thread(
        target=lambda: slow.append(_get(gateway, ws, "/public/x?delay_ms=2000"))
    )
    holder.start()
    try:
        r = wait_for(lambda: _get(gateway, ws, "/public/x"), 503, timeout=1.5)
        assert r.status_code == 503
        assert r.json() == {"error": "workspace_overloaded"}
        assert r.headers["Retry-After"] == "1"
        # Another workspace keeps answering meanwhile.
        assert _get(gateway, other, "/public/x").status_code == 200
    finally:
        holder.join(timeout=10)

    assert [r.status_code for r in slow] == [200]
    # The slot is given back with the slow answer.
    start = time.monotonic()
    assert wait_for(lambda: _get(gateway, ws, "/public/x"), 200).status_code == 200
    assert time.monotonic() - start < 1
