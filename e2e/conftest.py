"""Shared setup for the end-to-end tests.

The tests talk to the real Docker stack the way people and apps would: they
log in with GoTrue (password, then a TOTP code for the Platform Admin), use
the admin API of the control plane, and call APIs through the gateway. They
create their own objects with unique names and always delete them again,
so they never touch existing data.
"""

import base64
import hashlib
import hmac
import json
import os
import struct
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

# Addresses inside the Compose network (the e2e container runs there).
CONTROL_PLANE_URL = os.environ.get("CONTROL_PLANE_URL", "http://control-plane:8000")
GOTRUE_URL = os.environ.get("GOTRUE_URL", "http://auth:9999")
# Mailpit catches the emails GoTrue sends (invitations); its API lists them.
MAILPIT_URL = os.environ.get("MAILPIT_URL", "http://mailpit:8025")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:8080")
# The backend as the gateway reaches it.
MOCK_BACKEND_URL = os.environ.get("MOCK_BACKEND_URL", "http://mock-backend:9000")
# Its second copy (MT.10), for load balancing.
MOCK_BACKEND_2_URL = os.environ.get("MOCK_BACKEND_2_URL", "http://mock-backend-2:9000")
# Same host, a port nothing listens on: connection refused at once (a 502).
DEAD_BACKEND_URL = "http://mock-backend:9"

# What the runner needs from .env. Compose passes them through without
# requiring them (see docker-compose.yml), so they are checked here.
REQUIRED_ENV = ("PLATFORM_ADMIN_EMAIL", "PLATFORM_ADMIN_PASSWORD", "GOTRUE_SERVICE_ROLE_KEY")

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
    base: str = "/workspaces/default"  # the workspace the objects live in
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])
    _routes: list[str] = field(default_factory=list)
    _plans: dict[int, str] = field(default_factory=dict)  # rate limit -> plan id
    _extra_plans: list[str] = field(default_factory=list)  # plans with a quota
    _services: list[str] = field(default_factory=list)
    _consumers: list[str] = field(default_factory=list)

    def name(self, label: str) -> str:
        return f"e2e-{self.run_id}-{label}"

    def prefix(self, label: str) -> str:
        return f"/e2e-{self.run_id}/{label}"

    def service(
        self,
        label: str,
        upstream_url: str = MOCK_BACKEND_URL,
        timeout_ms: int = 5000,
        *,
        targets: list[dict[str, Any]] | None = None,
        **settings: Any,
    ) -> dict[str, Any]:
        """A service with one target at upstream_url, or the given targets;
        `settings` are other fields such as the health checks (MT.10)."""
        body = {
            "name": self.name(label),
            "targets": targets or [{"url": upstream_url}],
            "timeout_ms": timeout_ms,
            **settings,
        }
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
        **settings: Any,
    ) -> dict[str, Any]:
        """`settings` are other route fields, such as `cors` or `max_body_bytes` (MT.11)."""
        body = {
            "service_id": service["id"],
            "path_prefix": self.prefix(label),
            "methods": list(methods),
            "strip_prefix": strip_prefix,
            "auth_required": auth_required,
            **settings,
        }
        route = self._create("/routes", body)
        self._routes.append(route["id"])
        return route

    def plan(self, rate_limit_per_min: int, monthly_quota: int | None = None) -> str:
        """A plan of this run with that limit (V2.2: consumers' limits come from plans)."""
        if monthly_quota is not None or rate_limit_per_min not in self._plans:
            body = {
                "name": self.name(f"{rate_limit_per_min}-per-min-{uuid.uuid4().hex[:4]}"),
                "rate_limit_per_min": rate_limit_per_min,
                "monthly_quota": monthly_quota,
            }
            plan_id = self._create("/plans", body)["id"]
            if monthly_quota is not None:
                self._extra_plans.append(plan_id)
                return plan_id
            self._plans[rate_limit_per_min] = plan_id
        return self._plans[rate_limit_per_min]

    def consumer(
        self, label: str, rate_limit_per_min: int = 1000, *, plan_id: str | None = None
    ) -> dict[str, Any]:
        body = {
            "name": self.name(label),
            "email": "e2e@example.com",
            "plan_id": plan_id or self.plan(rate_limit_per_min),
        }
        consumer = self._create("/consumers", body)
        self._consumers.append(consumer["id"])
        return consumer

    def key(self, consumer: dict[str, Any]) -> Key:
        created = self._create(f"/consumers/{consumer['id']}/keys", {"name": "e2e"})
        return Key(id=created["id"], raw=created["key"])

    def _create(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self.admin.post(f"{self.base}{path}", json=body)
        assert r.status_code == 201, f"POST {path} {body}: {r.status_code} {r.text}"
        result: dict[str, Any] = r.json()
        return result

    def cleanup(self) -> None:
        # Routes first: a service that still has routes can't be deleted.
        # Deleting a consumer also deletes its keys; plans go after their consumers.
        for path, ids in (
            ("/routes", self._routes),
            ("/services", self._services),
            ("/consumers", self._consumers),
            ("/plans", [*self._plans.values(), *self._extra_plans]),
        ):
            for id_ in reversed(ids):
                r = self.admin.delete(f"{self.base}{path}/{id_}")
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


def pytest_configure(config: pytest.Config) -> None:
    """Stop at once, with a clear message, when a test credential is missing."""
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        pytest.exit(
            f"Set {', '.join(missing)} in .env: the e2e tests log in as the Platform Admin "
            "with a password (an invited admin without PLATFORM_ADMIN_PASSWORD can't run them).",
            returncode=2,
        )


# ---- GoTrue: logging in like the dashboard will

# The Platform Admin's MFA factor that the e2e tests enroll, use and remove.
# A person may hold several factors, so one you enrolled yourself is untouched.
E2E_FACTOR = "e2e"


@dataclass
class Session:
    access_token: str
    refresh_token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    @property
    def claims(self) -> dict[str, Any]:
        """The token's payload, read without checking it (the control plane checks)."""
        payload = self.access_token.split(".")[1]
        result: dict[str, Any] = json.loads(base64.urlsafe_b64decode(payload + "=="))
        return result


def totp_code(secret: str) -> str:
    """The 6-digit code an authenticator app shows (RFC 6238: HMAC-SHA1, 30 s steps)."""
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", int(time.time() // 30)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 1_000_000:06d}"


class GoTrue:
    """GoTrue's public API (as a person) and its admin API (with the service-role key)."""

    def __init__(self, url: str, service_role_key: str) -> None:
        self.http = httpx.Client(base_url=url, timeout=10)
        self._admin_headers = {"Authorization": f"Bearer {service_role_key}"}

    def sign_in(self, email: str, password: str) -> Session:
        r = self.http.post(
            "/token", params={"grant_type": "password"}, json={"email": email, "password": password}
        )
        assert r.status_code == 200, f"GoTrue login of {email} failed: {r.status_code} {r.text}"
        return Session(r.json()["access_token"], r.json()["refresh_token"])

    def refresh(self, refresh_token: str) -> httpx.Response:
        return self.http.post(
            "/token", params={"grant_type": "refresh_token"}, json={"refresh_token": refresh_token}
        )

    def add_totp(self, session: Session, name: str) -> tuple[str, str]:
        """Enroll a TOTP factor; returns (factor id, secret), as the QR code would."""
        r = self.http.post(
            "/factors", json={"factor_type": "totp", "friendly_name": name}, headers=session.headers
        )
        assert r.status_code == 200, f"MFA enroll failed: {r.status_code} {r.text}"
        return r.json()["id"], r.json()["totp"]["secret"]

    def verify_totp(self, session: Session, factor_id: str, secret: str) -> Session:
        """Challenge and verify the factor: the session steps up to aal2."""
        r = self.http.post(f"/factors/{factor_id}/challenge", json={}, headers=session.headers)
        assert r.status_code == 200, f"MFA challenge failed: {r.status_code} {r.text}"
        r = self.http.post(
            f"/factors/{factor_id}/verify",
            json={"challenge_id": r.json()["id"], "code": totp_code(secret)},
            headers=session.headers,
        )
        assert r.status_code == 200, f"MFA verify failed: {r.status_code} {r.text}"
        return Session(r.json()["access_token"], r.json()["refresh_token"])

    # Admin API (service-role key): only to set up and clean up test accounts.

    def accept_invitation(self, link: str) -> Session:
        """Open an invitation link as a browser would: GoTrue verifies it and
        redirects with the new session in the URL fragment."""
        link_parts = urlsplit(link)
        r = self.http.get(f"{link_parts.path}?{link_parts.query}", follow_redirects=False)
        assert r.status_code == 303, f"invitation link refused: {r.status_code} {r.text}"
        session = dict(parse_qsl(urlsplit(r.headers["location"]).fragment))
        assert "access_token" in session, f"no session in the redirect: {r.headers['location']}"
        return Session(session["access_token"], session["refresh_token"])

    def set_password(self, session: Session, password: str) -> None:
        r = self.http.put("/user", json={"password": password}, headers=session.headers)
        assert r.status_code == 200, f"setting the password failed: {r.status_code} {r.text}"

    def delete_account(self, user_id: str) -> None:
        self.http.delete(f"/admin/users/{user_id}", headers=self._admin_headers)

    def remove_factors(self, user_id: str, name: str) -> None:
        r = self.http.get(f"/admin/users/{user_id}/factors", headers=self._admin_headers)
        assert r.status_code == 200, r.text
        for factor in r.json():
            if factor["friendly_name"] == name:
                self.http.delete(
                    f"/admin/users/{user_id}/factors/{factor['id']}", headers=self._admin_headers
                )

    def ensure_account(self, email: str, password: str) -> None:
        """A confirmed, unbanned account with this password (made on the first run)."""
        r = self.http.post(
            "/admin/users",
            json={"email": email, "password": password, "email_confirm": True},
            headers=self._admin_headers,
        )
        if r.status_code == 200:
            return
        assert r.json().get("error_code") == "email_exists", r.text
        found = self.http.get(
            "/admin/users", params={"filter": email}, headers=self._admin_headers
        ).json()["users"]
        user_id = next(u["id"] for u in found if u["email"] == email)
        r = self.http.put(
            f"/admin/users/{user_id}",
            json={"password": password, "ban_duration": "none"},
            headers=self._admin_headers,
        )
        assert r.status_code == 200, r.text


@pytest.fixture(scope="session")
def gotrue() -> Iterator[GoTrue]:
    _wait_until_up(GOTRUE_URL)
    client = GoTrue(GOTRUE_URL, os.environ["GOTRUE_SERVICE_ROLE_KEY"])
    yield client
    client.http.close()


@pytest.fixture(scope="session")
def platform_admin_password_session(gotrue: GoTrue) -> Session:
    """The Platform Admin after the password step only (aal1)."""
    return gotrue.sign_in(os.environ["PLATFORM_ADMIN_EMAIL"], os.environ["PLATFORM_ADMIN_PASSWORD"])


@pytest.fixture(scope="session")
def platform_admin_session(
    gotrue: GoTrue, platform_admin_password_session: Session
) -> Iterator[Session]:
    """The Platform Admin after password and TOTP (aal2), as the dashboard will log in."""
    session = platform_admin_password_session
    user_id = session.claims["sub"]
    gotrue.remove_factors(user_id, E2E_FACTOR)  # left over by a run that was killed
    factor_id, secret = gotrue.add_totp(session, E2E_FACTOR)
    try:
        yield gotrue.verify_totp(session, factor_id, secret)
    finally:
        gotrue.remove_factors(user_id, E2E_FACTOR)


# A test person the tests manage through GoTrue's admin API. A fixed address,
# so repeated runs reuse one account instead of piling up new ones.
PERSON_EMAIL = "e2e-person@example.test"


@pytest.fixture
def person(gotrue: GoTrue) -> Session:
    """A signed-in person with no rights of their own, as a new team member will be."""
    password = uuid.uuid4().hex  # a new one each run; 32 characters
    gotrue.ensure_account(PERSON_EMAIL, password)
    return gotrue.sign_in(PERSON_EMAIL, password)


# The tests' own workspace, reused by every run (workspaces can be deleted only
# from MT.5); everything made inside it is deleted.
TENANT = "e2e-tenant"


@pytest.fixture
def owner(admin: httpx.Client, person: Session) -> Iterator[httpx.Client]:
    """The test person, as Owner of `e2e-tenant` (made by the Platform Admin)."""
    with httpx.Client(
        base_url=f"{CONTROL_PLANE_URL}/api/v1", headers=person.headers, timeout=10
    ) as client:
        assert client.get("/me").status_code == 200  # signed in once: now a user
        r = admin.post(
            "/workspaces",
            json={"name": "E2E tenant", "slug": TENANT, "owner_email": PERSON_EMAIL},
        )
        assert r.status_code in (201, 409), r.text  # 409: made by an earlier run
        yield client


@pytest.fixture(scope="session")
def admin(platform_admin_session: Session) -> Iterator[httpx.Client]:
    """The control plane's admin API, called with the Platform Admin's GoTrue token."""
    _wait_until_up(CONTROL_PLANE_URL)
    with httpx.Client(
        base_url=f"{CONTROL_PLANE_URL}/api/v1", headers=platform_admin_session.headers, timeout=10
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
