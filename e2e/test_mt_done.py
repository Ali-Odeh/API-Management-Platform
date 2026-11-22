"""MT.12: the MT definition of done (PLAN.md section 1), as one company setup.

A Platform Admin (with MFA) creates two workspaces, `acme` and `globex`, each
with its own Owner. Each Owner adds the same team member with a different
role: API Admin in acme, Viewer in globex. Both workspaces publish
`/v1/orders` on two backend targets; their consumers call `<slug>.localhost`
and reach only their own backends. Nothing crosses between them, every admin
change is in its own workspace's log, and the gateway refuses a suspended
workspace's traffic and serves a deleted one again once restored.

The two Owners are fixed test accounts (reused by every run, a new password
each time); the workspaces are new each run (slugs are never reused) and end
deleted, for the purge job.
"""

import uuid
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from conftest import (
    CONTROL_PLANE_URL,
    MOCK_BACKEND_2_URL,
    MOCK_BACKEND_URL,
    PERSON_EMAIL,
    GoTrue,
    wait_for,
)

CALLS = 20  # gateway calls per check; round robin over 2 targets reaches both


class Api:
    """One person's admin API client. Every write carries its own request id,
    so the test can find exactly that change in the audit log."""

    def __init__(self, access_token: str) -> None:
        self.http = httpx.Client(
            base_url=f"{CONTROL_PLANE_URL}/api/v1",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        self.changes: list[str] = []  # request ids of this person's writes

    def write(self, method: str, path: str, body: Any = None) -> httpx.Response:
        request_id = f"e2e-{uuid.uuid4().hex[:12]}"
        r = self.http.request(method, path, json=body, headers={"X-Request-ID": request_id})
        if r.is_success:
            self.changes.append(request_id)
        return r

    def created(self, path: str, body: Any) -> dict[str, Any]:
        r = self.write("POST", path, body)
        assert r.status_code == 201, f"POST {path}: {r.status_code} {r.text}"
        result: dict[str, Any] = r.json()
        return result


@dataclass
class Company:
    slug: str
    id: str
    owner: Api
    key: str = ""
    service_id: str = ""
    changes: list[str] = field(default_factory=list)  # every write in this workspace

    @property
    def host(self) -> str:
        return f"{self.slug}.localhost"

    @property
    def base(self) -> str:
        return f"/workspaces/{self.slug}"


def _signed_in(gotrue: GoTrue, email: str) -> Api:
    """A person with a fresh password, signed in and known to the control plane."""
    password = uuid.uuid4().hex
    gotrue.ensure_account(email, password)
    api = Api(gotrue.sign_in(email, password).access_token)
    assert api.http.get("/me").status_code == 200  # the first call makes their users row
    return api


def _publish_orders(api: Api, company: Company) -> None:
    """/v1/orders on two targets (each instance of the mock backend, under the
    workspace's own path), a consumer and its key: made by `api`."""
    base = company.base
    service = api.created(
        f"{base}/services",
        {
            "name": "orders",
            "targets": [
                {"url": f"{MOCK_BACKEND_URL}/{company.slug}"},
                {"url": f"{MOCK_BACKEND_2_URL}/{company.slug}"},
            ],
        },
    )
    api.created(
        f"{base}/routes",
        {"service_id": service["id"], "path_prefix": "/v1/orders", "methods": ["GET"]},
    )
    consumer = api.created(f"{base}/consumers", {"name": "shop", "email": "shop@example.com"})
    company.key = api.created(f"{base}/consumers/{consumer['id']}/keys", {"name": "prod"})["key"]
    company.service_id = service["id"]


@pytest.fixture(scope="module")
def companies(gotrue: GoTrue, admin: httpx.Client) -> Iterator[tuple[Company, Company, Api]]:
    """acme and globex, set up as the definition of done describes;
    (acme, globex, the team member)."""
    run = uuid.uuid4().hex[:6]
    member = _signed_in(gotrue, PERSON_EMAIL)
    made: list[Company] = []
    try:
        for name in ("acme", "globex"):
            owner_email = f"e2e-owner-{name}@example.test"
            owner = _signed_in(gotrue, owner_email)
            slug = f"e2e-{name}-{run}"
            # The Platform Admin's session has MFA (aal2): platform endpoints need it.
            body = {"name": name, "slug": slug, "owner_email": owner_email}
            r = admin.post("/workspaces", json=body)
            assert r.status_code == 201, r.text
            made.append(Company(slug=slug, id=r.json()["id"], owner=owner))
        acme, globex = made
        acme.owner.created(f"{acme.base}/members", {"email": PERSON_EMAIL, "role": "api_admin"})
        globex.owner.created(f"{globex.base}/members", {"email": PERSON_EMAIL, "role": "viewer"})
        acme.changes += acme.owner.changes
        globex.changes += globex.owner.changes

        # acme's API Admin builds acme's API; globex's Owner builds globex's
        # (the same person is only a Viewer there).
        before = len(member.changes)
        _publish_orders(member, acme)
        acme.changes += member.changes[before:]
        before = len(globex.owner.changes)
        _publish_orders(globex.owner, globex)
        globex.changes += globex.owner.changes[before:]
        yield acme, globex, member
    finally:
        for company in made:
            admin.post(f"{company.base}/unsuspend")  # 409 unless a test left it suspended
            admin.post(f"{company.base}/restore")  # 409 unless a test left it deleted
            r = admin.request("DELETE", company.base, json={"confirm": company.slug})
            assert r.status_code == 204, r.text
            company.owner.http.close()
        member.http.close()


def _call(gateway: httpx.Client, host: str, key: str) -> httpx.Response:
    return gateway.get("/v1/orders/7", headers={"Host": host, "X-API-Key": key})


def test_each_company_reaches_only_its_own_backends_on_both_targets(
    gateway: httpx.Client, companies: tuple[Company, Company, Api]
) -> None:
    acme, globex, _ = companies
    for company in (acme, globex):
        assert wait_for(lambda c=company: _call(gateway, c.host, c.key), 200).status_code == 200

        answers = [_call(gateway, company.host, company.key).json() for _ in range(CALLS)]

        assert {a["path"] for a in answers} == {f"/{company.slug}/v1/orders/7"}
        assert {a["headers"]["x-workspace-id"] for a in answers} == {company.id}
        # Equal weights: round robin alternates between the two instances.
        assert Counter(a["service"] for a in answers) == {
            "mock-backend": CALLS // 2,
            "mock-backend-2": CALLS // 2,
        }


def test_the_same_person_has_their_role_in_each_workspace(
    companies: tuple[Company, Company, Api],
) -> None:
    acme, globex, member = companies
    mine = {w["slug"]: w["role"] for w in member.http.get("/me/workspaces").json()["items"]}
    assert (mine[acme.slug], mine[globex.slug]) == ("api_admin", "viewer")

    # A Viewer reads but never writes.
    assert member.http.get(f"{globex.base}/services").status_code == 200
    r = member.write("POST", f"{globex.base}/consumers", {"name": "x", "email": "x@example.com"})
    assert (r.status_code, r.json()) == (403, {"detail": "permission_denied"})
    # An API Admin manages the API but not the team.
    r = member.write("POST", f"{acme.base}/members", {"email": "x@example.test", "role": "viewer"})
    assert (r.status_code, r.json()) == (403, {"detail": "permission_denied"})


def test_nothing_crosses_between_the_workspaces(
    gateway: httpx.Client, companies: tuple[Company, Company, Api]
) -> None:
    acme, globex, _ = companies
    for this, other in ((acme, globex), (globex, acme)):
        # A key works only on its own workspace's host.
        r = _call(gateway, other.host, this.key)
        assert (r.status_code, r.json()) == (401, {"error": "invalid_api_key"})
        # An Owner sees nothing of the other workspace: it doesn't exist for them.
        assert this.owner.http.get(f"{other.base}/services").status_code == 404
        assert this.owner.http.get(f"{other.base}/audit-events").status_code == 404
        # Nor can they point at the other's objects from their own.
        r = this.owner.write(
            "POST",
            f"{this.base}/routes",
            {"service_id": other.service_id, "path_prefix": "/stolen", "methods": ["GET"]},
        )
        assert r.status_code == 422, r.text


def test_every_admin_change_is_in_its_own_workspaces_log_only(
    companies: tuple[Company, Company, Api],
) -> None:
    acme, globex, _ = companies
    for this, other in ((acme, globex), (globex, acme)):
        r = this.owner.http.get(f"{this.base}/audit-events", params={"page_size": 100})
        assert r.status_code == 200, r.text
        log = r.json()["items"]
        logged = {e["request_id"] for e in log}

        assert set(this.changes) <= logged, "a change is missing from its workspace's log"
        assert not set(other.changes) & logged, "the other workspace's change is in this log"
        assert {e["workspace_id"] for e in log} == {this.id}
    # Who did what: the member's changes in acme are theirs, as API Admin.
    log = acme.owner.http.get(f"{acme.base}/audit-events", params={"actor": PERSON_EMAIL}).json()
    assert sorted(e["action"] for e in log["items"]) == [
        "consumer.create",
        "key.create",
        "route.create",
        "service.create",
    ]


def test_suspended_traffic_is_refused_and_a_deleted_workspace_comes_back_on_restore(
    gateway: httpx.Client, admin: httpx.Client, companies: tuple[Company, Company, Api]
) -> None:
    acme, globex, _ = companies
    assert wait_for(lambda: _call(gateway, acme.host, acme.key), 200).status_code == 200

    assert admin.post(f"{acme.base}/suspend", json={"reason": "e2e"}).status_code == 200
    r = wait_for(lambda: _call(gateway, acme.host, acme.key), 403)
    assert (r.status_code, r.json()) == (403, {"error": "workspace_suspended"})
    assert _call(gateway, globex.host, globex.key).status_code == 200
    assert admin.post(f"{acme.base}/unsuspend").status_code == 200

    # Its Owner deletes it: its host is gone from the gateway...
    r = acme.owner.http.request("DELETE", acme.base, json={"confirm": acme.slug})
    assert r.status_code == 204, r.text
    r = wait_for(lambda: _call(gateway, acme.host, acme.key), 404)
    assert (r.status_code, r.json()) == (404, {"error": "workspace_not_found"})
    assert _call(gateway, globex.host, globex.key).status_code == 200

    # ...until the Platform Admin restores it, with everything it had.
    assert admin.post(f"{acme.base}/restore").status_code == 200
    r = wait_for(lambda: _call(gateway, acme.host, acme.key), 200)
    assert r.status_code == 200, r.text
    assert r.json()["headers"]["x-workspace-id"] == acme.id
