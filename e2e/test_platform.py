"""The MVP story, end to end (PLAN.md section 1).

An admin registers a service and a route, creates a consumer with an API key,
and that consumer calls the backend through the gateway. The request is
authenticated, rate-limited and logged. Bad keys get 401, and clients over the
limit get 429. Plus the other answers the gateway gives itself.
"""

import time
from typing import Any

import httpx
import pytest

from conftest import DEAD_BACKEND_URL, Key, World, wait_for


@pytest.fixture(scope="module")
def shop(world: World) -> dict[str, Any]:
    """The happy path: an orders service, a protected route, an app with a key."""
    service = world.service("orders")
    route = world.route(service, "orders", methods=("GET", "POST"))
    consumer = world.consumer("mobile-app")
    return {"route": route, "consumer": consumer, "key": world.key(consumer)}


def get(gateway: httpx.Client, path: str, key: Key | None = None, **kw: Any) -> httpx.Response:
    headers = {"X-API-Key": key.raw} if key else {}
    headers.update(kw.pop("headers", {}))
    return gateway.get(path, headers=headers, **kw)


def error(r: httpx.Response) -> str:
    code: str = r.json()["error"]
    return code


# ---- The main story ----


def test_valid_key_reaches_the_backend(gateway: httpx.Client, shop: dict[str, Any]) -> None:
    path = shop["route"]["path_prefix"] + "/7"
    r = wait_for(
        lambda: get(gateway, path, shop["key"], headers={"X-Request-ID": "e2e-story-1"}), 200
    )

    assert r.status_code == 200, r.text
    echo = r.json()  # the mock backend shows what it received
    assert echo["path"] == path
    assert echo["headers"]["x-consumer-id"] == shop["consumer"]["id"]
    assert echo["headers"]["x-request-id"] == "e2e-story-1"
    assert "x-api-key" not in echo["headers"], "the backend must never see the API key"
    assert r.headers["X-Request-ID"] == "e2e-story-1"
    assert r.headers["X-RateLimit-Limit"] == "1000"


def test_post_body_reaches_the_backend(gateway: httpx.Client, shop: dict[str, Any]) -> None:
    path = shop["route"]["path_prefix"]
    r = wait_for(
        lambda: gateway.post(path, json={"item": "book"}, headers={"X-API-Key": shop["key"].raw}),
        200,
    )
    assert r.status_code == 200, r.text
    assert r.json()["method"] == "POST"
    assert r.json()["body"] == '{"item":"book"}'


# ---- Bad keys: 401 ----


def test_missing_key_is_401(gateway: httpx.Client, shop: dict[str, Any]) -> None:
    r = wait_for(lambda: get(gateway, shop["route"]["path_prefix"]), 401)
    assert r.status_code == 401
    assert error(r) == "missing_api_key"
    assert "WWW-Authenticate" in r.headers


def test_wrong_key_is_401(gateway: httpx.Client, shop: dict[str, Any]) -> None:
    wrong = Key(id="", raw="ak_live_" + "0" * 32)
    r = wait_for(lambda: get(gateway, shop["route"]["path_prefix"], wrong), 401)
    assert r.status_code == 401
    assert error(r) == "invalid_api_key"


# ---- Admin actions reach the gateway in about a second ----


def test_blocked_consumer_gets_403(
    gateway: httpx.Client, world: World, shop: dict[str, Any]
) -> None:
    consumer = world.consumer("to-block")
    key = world.key(consumer)
    path = shop["route"]["path_prefix"]
    assert wait_for(lambda: get(gateway, path, key), 200).status_code == 200

    r = world.admin.patch(f"{world.base}/consumers/{consumer['id']}", json={"status": "blocked"})
    assert r.status_code == 200, r.text

    r = wait_for(lambda: get(gateway, path, key), 403)
    assert r.status_code == 403
    assert error(r) == "consumer_blocked"


def test_revoked_key_gets_401(gateway: httpx.Client, world: World, shop: dict[str, Any]) -> None:
    consumer = world.consumer("to-revoke")
    key = world.key(consumer)
    path = shop["route"]["path_prefix"]
    assert wait_for(lambda: get(gateway, path, key), 200).status_code == 200

    assert world.admin.delete(f"{world.base}/keys/{key.id}").status_code == 204

    r = wait_for(lambda: get(gateway, path, key), 401)
    assert r.status_code == 401
    assert error(r) == "invalid_api_key"


def test_new_route_works_within_a_second(
    gateway: httpx.Client, world: World, shop: dict[str, Any]
) -> None:
    service = world.service("fresh")
    start = time.monotonic()
    route = world.route(service, "fresh")

    r = wait_for(lambda: get(gateway, route["path_prefix"], shop["key"]), 200)

    took = time.monotonic() - start
    assert r.status_code == 200, r.text
    assert took < 1.0, f"the new route took {took:.2f} s to reach the gateway"


# ---- Rate limit: 429 ----


def test_over_the_limit_is_429(gateway: httpx.Client, world: World, shop: dict[str, Any]) -> None:
    consumer = world.consumer("limited", rate_limit_per_min=3)
    key = world.key(consumer)
    path = shop["route"]["path_prefix"]

    # Requests refused while the key is still unknown (401) cost no budget,
    # so the first 200 is request 1 of 3.
    statuses = [wait_for(lambda: get(gateway, path, key), 200).status_code]
    statuses += [get(gateway, path, key).status_code for _ in range(3)]

    assert statuses == [200, 200, 200, 429]
    r = get(gateway, path, key)
    assert error(r) == "rate_limit_exceeded"
    assert r.headers["X-RateLimit-Limit"] == "3"
    assert r.headers["X-RateLimit-Remaining"] == "0"
    assert int(r.headers["Retry-After"]) >= 1


# ---- Answers the gateway gives itself ----


def test_unknown_path_is_404(gateway: httpx.Client, world: World) -> None:
    r = gateway.get(world.prefix("nothing-here"))
    assert r.status_code == 404
    assert error(r) == "route_not_found"


def test_wrong_method_is_405(gateway: httpx.Client, shop: dict[str, Any]) -> None:
    r = wait_for(lambda: gateway.delete(shop["route"]["path_prefix"]), 405)
    assert r.status_code == 405
    assert error(r) == "method_not_allowed"
    assert r.headers["Allow"] == "GET, POST"


def test_dead_backend_is_502(gateway: httpx.Client, world: World, shop: dict[str, Any]) -> None:
    route = world.route(world.service("dead", upstream_url=DEAD_BACKEND_URL), "dead")
    r = wait_for(lambda: get(gateway, route["path_prefix"], shop["key"]), 502)
    assert r.status_code == 502, r.text
    assert error(r) == "bad_gateway"


def test_slow_backend_is_504_at_the_route_timeout(
    gateway: httpx.Client, world: World, shop: dict[str, Any]
) -> None:
    route = world.route(world.service("slow", timeout_ms=500), "slow")
    path = route["path_prefix"] + "?delay_ms=2000"  # the mock backend answers 2 s late
    wait_for(lambda: get(gateway, path, shop["key"]), 504, timeout=6)

    start = time.monotonic()
    r = get(gateway, path, shop["key"])
    took = time.monotonic() - start

    assert r.status_code == 504
    assert error(r) == "gateway_timeout"
    assert 0.4 < took < 1.5, f"504 after {took:.2f} s, want about the 0.5 s route timeout"


# ---- Route options ----


def test_strip_prefix(gateway: httpx.Client, world: World, shop: dict[str, Any]) -> None:
    route = world.route(world.service("stripped"), "stripped", strip_prefix=True)
    r = wait_for(lambda: get(gateway, route["path_prefix"] + "/7", shop["key"]), 200)
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "/7"


def test_public_route_needs_no_key(gateway: httpx.Client, world: World) -> None:
    route = world.route(world.service("public"), "public", auth_required=False)
    r = wait_for(lambda: gateway.get(route["path_prefix"]), 200)
    assert r.status_code == 200, r.text
    assert "X-RateLimit-Limit" not in r.headers  # no consumer, so no limit


def test_gateway_health(gateway: httpx.Client) -> None:
    r = gateway.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
