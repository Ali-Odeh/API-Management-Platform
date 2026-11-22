"""MT.7 stories: each workspace on its own subdomain, never mixed.

Each run makes two workspaces (new slugs: slugs are never reused) that both
publish /v1/orders, each to its own backend path, and calls the gateway
with `Host: <slug>.localhost`, as a client of `<slug>.<gateway>` would. The
workspaces are deleted at the end (the purge job removes their data later).
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest

from conftest import MOCK_BACKEND_URL, PERSON_EMAIL, Session, wait_for


@dataclass
class Tenant:
    slug: str
    id: str
    key: str

    @property
    def host(self) -> str:
        return f"{self.slug}.localhost"


def _make_tenant(admin: httpx.Client, slug: str) -> Tenant:
    """A workspace with a protected /v1/orders to its own backend path, and a key.

    Made by the Platform Admin (MFA): platform power may act in any workspace.
    """
    r = admin.post("/workspaces", json={"name": slug, "slug": slug, "owner_email": PERSON_EMAIL})
    assert r.status_code == 201, r.text
    workspace_id = r.json()["id"]
    base = f"/workspaces/{slug}"
    r = admin.post(
        f"{base}/services",
        json={"name": "orders", "targets": [{"url": f"{MOCK_BACKEND_URL}/from-{slug}"}]},
    )
    assert r.status_code == 201, r.text
    r = admin.post(
        f"{base}/routes",
        json={"service_id": r.json()["id"], "path_prefix": "/v1/orders", "methods": ["GET"]},
    )
    assert r.status_code == 201, r.text
    r = admin.post(f"{base}/consumers", json={"name": "app", "email": "e2e@example.com"})
    assert r.status_code == 201, r.text
    r = admin.post(f"{base}/consumers/{r.json()['id']}/keys", json={"name": "e2e"})
    assert r.status_code == 201, r.text
    return Tenant(slug=slug, id=workspace_id, key=r.json()["key"])


@pytest.fixture
def tenants(admin: httpx.Client, person: Session) -> Iterator[tuple[Tenant, Tenant]]:
    """Two fresh workspaces, owned by the test person (a user since `person`)."""
    run = uuid.uuid4().hex[:6]
    made: list[Tenant] = []
    try:
        for side in ("a", "b"):
            made.append(_make_tenant(admin, f"e2e-{side}-{run}"))
        yield made[0], made[1]
    finally:
        for tenant in made:
            admin.post(
                f"/workspaces/{tenant.slug}/unsuspend"
            )  # 409 unless a test left it suspended
            r = admin.request("DELETE", f"/workspaces/{tenant.slug}", json={"confirm": tenant.slug})
            assert r.status_code == 204, r.text


def _call(gateway: httpx.Client, host: str, key: str) -> httpx.Response:
    return gateway.get("/v1/orders/7", headers={"Host": host, "X-API-Key": key})


def test_the_same_path_reaches_each_workspaces_own_backend(
    gateway: httpx.Client, tenants: tuple[Tenant, Tenant]
) -> None:
    for tenant in tenants:
        r = wait_for(lambda t=tenant: _call(gateway, t.host, t.key), 200)

        assert r.status_code == 200, r.text
        echo = r.json()  # the mock backend shows what it received
        assert echo["path"] == f"/from-{tenant.slug}/v1/orders/7"
        # The backend learns the workspace from the gateway alone.
        assert echo["headers"]["x-workspace-id"] == tenant.id


def test_a_key_works_only_on_its_own_workspaces_host(
    gateway: httpx.Client, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    assert wait_for(lambda: _call(gateway, b.host, b.key), 200).status_code == 200

    r = _call(gateway, b.host, a.key)  # a valid key, of the other workspace

    assert r.status_code == 401
    assert r.json() == {"error": "invalid_api_key"}


def test_an_unknown_subdomain_is_404(gateway: httpx.Client) -> None:
    r = gateway.get("/v1/orders", headers={"Host": f"nobody-{uuid.uuid4().hex[:6]}.localhost"})
    assert r.status_code == 404
    assert r.json() == {"error": "workspace_not_found"}


def test_a_suspended_workspace_is_refused_within_one_config_update(
    gateway: httpx.Client, admin: httpx.Client, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    assert wait_for(lambda: _call(gateway, a.host, a.key), 200).status_code == 200

    assert admin.post(f"/workspaces/{a.slug}/suspend", json={"reason": "e2e"}).status_code == 200
    r = wait_for(lambda: _call(gateway, a.host, a.key), 403)

    assert r.status_code == 403
    assert r.json() == {"error": "workspace_suspended"}
    assert _call(gateway, b.host, b.key).status_code == 200  # the other workspace is untouched

    assert admin.post(f"/workspaces/{a.slug}/unsuspend").status_code == 200
    assert wait_for(lambda: _call(gateway, a.host, a.key), 200).status_code == 200
