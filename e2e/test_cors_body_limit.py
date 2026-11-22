"""MT.11 stories: browser pages calling APIs (CORS), and bodies too large to forward.

The gateway answers a browser's preflight itself, before the API key is
checked, and adds the CORS headers to normal answers for the origins a route
allows. A body over the route's limit gets 413 and never reaches the
backend as a complete request, whether its length was announced or not.
"""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from conftest import World, wait_for

APP = "https://app.example.com"


@pytest.fixture(scope="module")
def api(world: World, gateway: httpx.Client) -> dict[str, Any]:
    """A protected route with CORS for APP (credentials allowed) and a 1 KiB
    body limit, and a key for it."""
    service = world.service("cors")
    route = world.route(
        service,
        "cors",
        methods=("GET", "POST"),
        max_body_bytes=1024,
        cors={
            "origins": [APP],
            "headers": ["Content-Type", "X-API-Key"],
            "credentials": True,
            "max_age": 600,
        },
    )
    key = world.key(world.consumer("cors-app"))
    path = route["path_prefix"]
    ready = wait_for(lambda: gateway.get(path, headers={"X-API-Key": key.raw}), 200)
    assert ready.status_code == 200, ready.text
    return {"path": path, "key": key}


def _preflight(gateway: httpx.Client, path: str, origin: str) -> httpx.Response:
    return gateway.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, x-api-key",
        },
    )


def test_a_preflight_from_an_allowed_origin_is_answered_by_the_gateway(
    gateway: httpx.Client, api: dict[str, Any]
) -> None:
    r = _preflight(gateway, api["path"], APP)  # no API key: browsers never send one here

    assert r.status_code == 204
    assert r.content == b""  # not the backend's echo: never forwarded
    assert r.headers["Access-Control-Allow-Origin"] == APP
    assert r.headers["Access-Control-Allow-Methods"] == "GET, POST"  # the route's methods
    assert r.headers["Access-Control-Allow-Headers"] == "Content-Type, X-API-Key"
    assert r.headers["Access-Control-Allow-Credentials"] == "true"
    assert r.headers["Access-Control-Max-Age"] == "600"


def test_a_preflight_from_another_origin_gets_no_allow_headers(
    gateway: httpx.Client, api: dict[str, Any]
) -> None:
    r = _preflight(gateway, api["path"], "https://evil.example")

    assert r.status_code == 204
    assert "Access-Control-Allow-Origin" not in r.headers  # so the browser blocks the call


def test_a_credentialed_call_from_the_page(gateway: httpx.Client, api: dict[str, Any]) -> None:
    r = gateway.get(
        api["path"], headers={"Origin": APP, "X-API-Key": api["key"].raw, "Cookie": "s=1"}
    )

    assert r.status_code == 200
    assert r.json()["service"] == "mock-backend"  # it reached the backend
    assert r.headers["Access-Control-Allow-Origin"] == APP
    assert r.headers["Access-Control-Allow-Credentials"] == "true"
    assert "Origin" in r.headers["Vary"]


def test_a_body_with_its_length_over_the_limit_is_413(
    gateway: httpx.Client, api: dict[str, Any]
) -> None:
    r = gateway.post(api["path"], content=b"x" * 2048, headers={"X-API-Key": api["key"].raw})

    assert r.status_code == 413
    assert r.json() == {"error": "request_too_large"}


def test_a_body_without_a_length_over_the_limit_is_413(
    gateway: httpx.Client, api: dict[str, Any]
) -> None:
    def chunks() -> Iterator[bytes]:  # no Content-Length: sent chunked
        for _ in range(8):
            yield b"x" * 512

    r = gateway.post(api["path"], content=chunks(), headers={"X-API-Key": api["key"].raw})

    assert "content-length" not in {k.lower() for k in r.request.headers}
    assert r.status_code == 413
    assert r.json() == {"error": "request_too_large"}


def test_a_body_within_the_limit_reaches_the_backend(
    gateway: httpx.Client, api: dict[str, Any]
) -> None:
    r = gateway.post(api["path"], content=b"x" * 1024, headers={"X-API-Key": api["key"].raw})

    assert r.status_code == 200
    assert r.json()["body"] == "x" * 1024
