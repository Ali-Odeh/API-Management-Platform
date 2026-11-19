"""Shared setup for the end-to-end tests.

The tests talk to the real Docker stack the way an admin and an app would:
the admin API of the control plane, and API calls through the gateway. They
create their own objects with unique names and always delete them again,
so they never touch existing data.
"""

import os
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

# Addresses inside the Compose network (the e2e container runs there).
CONTROL_PLANE_URL = os.environ.get("CONTROL_PLANE_URL", "http://control-plane:8000")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:8080")
# The backend as the gateway reaches it.
MOCK_BACKEND_URL = os.environ.get("MOCK_BACKEND_URL", "http://mock-backend:9000")
# Same host, a port nothing listens on: connection refused at once (a 502).
DEAD_BACKEND_URL = "http://mock-backend:9"

STACK_START_TIMEOUT = 90.0  # seconds; containers may still be starting
CONFIG_WAIT = 3.0  # seconds; config changes usually reach the gateway in ~70 ms


@dataclass
class Key:
    id: str
    raw: str  # shown once at creation, like for a real admin


@dataclass
class World:
    """Creates test objects through the admin API and deletes them at the end."""

    admin: httpx.Client
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])
    _routes: list[str] = field(default_factory=list)
    _services: list[str] = field(default_factory=list)
    _consumers: list[str] = field(default_factory=list)

    def name(self, label: str) -> str:
        return f"e2e-{self.run_id}-{label}"

    def prefix(self, label: str) -> str:
        return f"/e2e-{self.run_id}/{label}"

    def service(
        self, label: str, upstream_url: str = MOCK_BACKEND_URL, timeout_ms: int = 5000
    ) -> dict[str, Any]:
        body = {"name": self.name(label), "upstream_url": upstream_url, "timeout_ms": timeout_ms}
        svc = self._create("/services", body)
        self._services.append(svc["id"])
        return svc

    def route(
        self,
        service: dict[str, Any],
        label: str,
        *,
        methods: tuple[str, ...] = ("GET",),
        strip_prefix: bool = False,
        auth_required: bool = True,
    ) -> dict[str, Any]:
        body = {
            "service_id": service["id"],
            "path_prefix": self.prefix(label),
            "methods": list(methods),
            "strip_prefix": strip_prefix,
            "auth_required": auth_required,
        }
        route = self._create("/routes", body)
        self._routes.append(route["id"])
        return route

    def consumer(self, label: str, rate_limit_per_min: int = 1000) -> dict[str, Any]:
        body = {
            "name": self.name(label),
            "email": "e2e@example.com",
            "rate_limit_per_min": rate_limit_per_min,
        }
        consumer = self._create("/consumers", body)
        self._consumers.append(consumer["id"])
        return consumer

    def key(self, consumer: dict[str, Any]) -> Key:
        created = self._create(f"/consumers/{consumer['id']}/keys", {"name": "e2e"})
        return Key(id=created["id"], raw=created["key"])

    def _create(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self.admin.post(path, json=body)
        assert r.status_code == 201, f"POST {path} {body}: {r.status_code} {r.text}"
        result: dict[str, Any] = r.json()
        return result

    def cleanup(self) -> None:
        # Routes first: a service that still has routes can't be deleted.
        # Deleting a consumer also deletes its keys.
        for path, ids in (
            ("/routes", self._routes),
            ("/services", self._services),
            ("/consumers", self._consumers),
        ):
            for id_ in reversed(ids):
                r = self.admin.delete(f"{path}/{id_}")
                assert r.status_code in (204, 404), f"cleanup DELETE {path}/{id_}: {r.text}"


def wait_for(
    call: Callable[[], httpx.Response], status: int, timeout: float = CONFIG_WAIT
) -> httpx.Response:
    """Repeat call until it answers with status, or the time is up.

    Admin changes reach the gateway in milliseconds (long polling), so this
    usually returns at once; it just never relies on a fixed sleep.
    """
    deadline = time.monotonic() + timeout
    while True:
        r = call()
        if r.status_code == status or time.monotonic() > deadline:
            return r
        time.sleep(0.05)


def _wait_until_up(url: str) -> None:
    deadline = time.monotonic() + STACK_START_TIMEOUT
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/health", timeout=2).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.5)
    pytest.exit(f"{url} is not up after {STACK_START_TIMEOUT:.0f} s; is the stack running?")


@pytest.fixture(scope="session")
def admin() -> Iterator[httpx.Client]:
    _wait_until_up(CONTROL_PLANE_URL)
    login = {"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]}
    r = httpx.post(f"{CONTROL_PLANE_URL}/api/v1/auth/login", json=login, timeout=10)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    with httpx.Client(
        base_url=f"{CONTROL_PLANE_URL}/api/v1", headers=headers, timeout=10
    ) as client:
        yield client


@pytest.fixture(scope="session")
def gateway() -> Iterator[httpx.Client]:
    _wait_until_up(GATEWAY_URL)
    with httpx.Client(base_url=GATEWAY_URL, timeout=10) as client:
        yield client


@pytest.fixture(scope="session")
def world(admin: httpx.Client) -> Iterator[World]:
    w = World(admin)
    try:
        yield w
    finally:
        w.cleanup()  # runs even when tests failed
