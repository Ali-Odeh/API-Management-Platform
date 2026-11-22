"""MT.10 stories: a service on two backends, and traffic that avoids a broken one.

The stack runs two copies of the mock backend (`mock-backend`,
`mock-backend-2`); each names itself in its echo. A test makes the second
one act as if it were down (PUT /_mock/state), and always brings it back.
"""

import time
from collections import Counter
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from conftest import MOCK_BACKEND_2_URL, MOCK_BACKEND_URL, World, wait_for

# Quick checks, so the stories see health changes in seconds: one active
# check a second, out after 2 failures, back after 2 passes.
FAST_CHECKS: dict[str, Any] = {
    "hc_interval_s": 1,
    "hc_unhealthy_threshold": 2,
    "hc_healthy_threshold": 2,
    "passive_failures": 3,
    "passive_eject_s": 2,
}


@pytest.fixture
def second_backend() -> Iterator[httpx.Client]:
    """The second mock backend's own API; it is always up again afterwards."""
    with httpx.Client(base_url=MOCK_BACKEND_2_URL, timeout=5) as client:
        try:
            yield client
        finally:
            client.put("/_mock/state", json={"up": True})


def _set_up(backend: httpx.Client, up: bool) -> None:
    r = backend.put("/_mock/state", json={"up": up})
    assert r.status_code == 200, r.text


def _answered_by(gateway: httpx.Client, path: str) -> str | None:
    """Which backend answered (None: not a backend's echo)."""
    r = gateway.get(path)
    return r.json().get("service") if r.status_code == 200 else None


def _until(cond: Any, timeout: float) -> float:
    """Poll cond until it holds; returns the seconds it took."""
    start = time.monotonic()
    while not cond():
        if time.monotonic() - start > timeout:
            pytest.fail(f"not within {timeout} s")
        time.sleep(0.1)
    return time.monotonic() - start


def test_traffic_is_spread_over_two_targets_by_weight(gateway: httpx.Client, world: World) -> None:
    service = world.service(
        "spread",
        targets=[{"url": MOCK_BACKEND_URL, "weight": 3}, {"url": MOCK_BACKEND_2_URL, "weight": 1}],
    )
    path = world.route(service, "spread", auth_required=False)["path_prefix"]
    assert wait_for(lambda: gateway.get(path), 200).status_code == 200

    # Smooth weighted round robin repeats every 4 picks (3 + 1): any 40 in a
    # row hold exactly 30 and 10.
    answered = Counter(_answered_by(gateway, path) for _ in range(40))

    assert answered == {"mock-backend": 30, "mock-backend-2": 10}


def test_traffic_moves_off_a_stopped_target_and_back(
    gateway: httpx.Client, world: World, second_backend: httpx.Client
) -> None:
    service = world.service(
        "failover",
        targets=[{"url": MOCK_BACKEND_URL}, {"url": MOCK_BACKEND_2_URL}],
        **FAST_CHECKS,
    )
    path = world.route(service, "failover", auth_required=False)["path_prefix"]
    assert wait_for(lambda: gateway.get(path), 200).status_code == 200
    assert {_answered_by(gateway, path) for _ in range(4)} == {"mock-backend", "mock-backend-2"}

    _set_up(second_backend, False)
    # Off it within the thresholds (2 failed checks a second apart, or 3
    # failed requests): then every request reaches the first backend.
    took = _until(
        lambda: all(_answered_by(gateway, path) == "mock-backend" for _ in range(10)), timeout=8
    )
    assert took < 6, f"took {took:.1f} s to move off the stopped target"

    _set_up(second_backend, True)
    # Back after 2 passed checks.
    _until(lambda: "mock-backend-2" in {_answered_by(gateway, path) for _ in range(4)}, timeout=8)


def test_no_healthy_target_is_503(
    gateway: httpx.Client, world: World, second_backend: httpx.Client
) -> None:
    service = world.service("lonely", upstream_url=MOCK_BACKEND_2_URL, **FAST_CHECKS)
    path = world.route(service, "lonely", auth_required=False)["path_prefix"]
    assert wait_for(lambda: gateway.get(path), 200).status_code == 200

    _set_up(second_backend, False)

    def refused_by_the_gateway() -> bool:
        # Not the backend's own 503 (mock_down): the gateway's, with no target left.
        r = gateway.get(path)
        return r.status_code == 503 and r.json() == {"error": "no_healthy_upstream"}

    _until(refused_by_the_gateway, timeout=8)
