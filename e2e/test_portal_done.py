"""P.2: the Portal definition of done (PLAN.md section 1), as one setup.

A developer signs up on the portal (its own GoTrue, open sign-up, the
confirmation email in Mailpit), browses a workspace's catalog and sees only
its published APIs with their OpenAPI documents; a draft is hidden, a
deprecated API is marked. They create an app on a plan that needs approval
(it waits for an API Admin) and one on a plan that doesn't, get a key, call
the API through the gateway until the monthly quota is used, see their own
usage and remaining quota, and revoke the key. Another developer never sees
their apps. Every developer change is in the workspace's audit log. The
workspace is new each run and ends deleted; the developers' accounts and
emails are deleted.
"""

import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from conftest import (
    CONFIG_WAIT,
    CONTROL_PLANE_URL,
    MOCK_BACKEND_URL,
    PERSON_EMAIL,
    Developer,
    Session,
)

QUOTA = 3
WAIT = 20.0  # seconds: the usage-ingest worker stores within a second or two

DOC: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "Orders", "version": "1.0"},
    "paths": {"/orders": {"get": {"responses": {"200": {"description": "The orders"}}}}},
}


@pytest.fixture
def portal() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=f"{CONTROL_PLANE_URL}/api/v1/portal", timeout=10) as client:
        yield client


@pytest.fixture
def shop(admin: httpx.Client, person: Session) -> Iterator[dict[str, Any]]:
    """A new workspace with /orders, the Orders API (published) and Labs (a draft),
    and three plans: Free (offered, a small quota), Partner (offered, needs
    approval) and Internal (not offered)."""
    slug = f"e2e-portal-{uuid.uuid4().hex[:6]}"
    r = admin.post("/workspaces", json={"name": slug, "slug": slug, "owner_email": PERSON_EMAIL})
    assert r.status_code == 201, r.text
    base = f"/workspaces/{slug}"
    try:
        service = admin.post(
            f"{base}/services", json={"name": "api", "targets": [{"url": MOCK_BACKEND_URL}]}
        ).json()
        route = admin.post(
            f"{base}/routes",
            json={"service_id": service["id"], "path_prefix": "/orders", "methods": ["GET"]},
        ).json()
        orders = admin.post(
            f"{base}/apis",
            json={"name": "Orders", "slug": "orders", "openapi": DOC, "route_ids": [route["id"]]},
        ).json()
        assert admin.patch(f"{base}/apis/{orders['id']}", json={"status": "published"}).is_success
        labs = admin.post(f"{base}/apis", json={"name": "Labs", "slug": "labs"}).json()
        plans = {
            name: admin.post(f"{base}/plans", json=body).json()["id"]
            for name, body in (
                ("free", {"name": "Free", "rate_limit_per_min": 600, "monthly_quota": QUOTA,
                          "portal_visible": True}),
                ("partner", {"name": "Partner", "rate_limit_per_min": 600,
                             "portal_visible": True, "requires_approval": True}),
                ("internal", {"name": "Internal", "rate_limit_per_min": 600}),
            )
        }  # fmt: skip
        yield {
            "slug": slug,
            "base": base,
            "host": f"{slug}.localhost",
            "orders": orders["id"],
            "labs": labs["id"],
            **plans,
        }
    finally:
        r = admin.request("DELETE", base, json={"confirm": slug})
        assert r.status_code == 204, r.text


def _until(what: str, check: Callable[[], Any], wait: float = WAIT) -> Any:
    deadline = time.monotonic() + wait
    while True:
        if result := check():
            return result
        if time.monotonic() > deadline:
            pytest.fail(f"not within {wait:.0f} s: {what}")
        time.sleep(0.2)


def test_the_portal_definition_of_done(
    new_developer: Callable[[], Developer],
    portal: httpx.Client,
    gateway: httpx.Client,
    admin: httpx.Client,
    shop: dict[str, Any],
) -> None:
    dev = new_developer()  # signs up, confirms through the email, logs in
    catalog = f"/workspaces/{shop['slug']}"

    # The catalog: the published API with its document; the draft is hidden.
    apis = portal.get(f"{catalog}/apis", headers=dev.headers).json()["items"]
    assert [a["name"] for a in apis] == ["Orders"]
    assert (
        portal.get(f"{catalog}/apis/{shop['orders']}", headers=dev.headers).json()["openapi"] == DOC
    )
    assert portal.get(f"{catalog}/apis/{shop['labs']}", headers=dev.headers).status_code == 404
    offered = portal.get(f"{catalog}/plans", headers=dev.headers).json()["items"]
    assert [p["name"] for p in offered] == ["Free", "Partner"]

    # An app on a plan that needs approval waits; an API Admin approves it.
    partner = portal.post(
        "/apps",
        json={"workspace": shop["slug"], "api_id": shop["orders"], "plan_id": shop["partner"],
              "name": "Partner app"},
        headers=dev.headers,
    ).json()  # fmt: skip
    assert partner["status"] == "pending"
    r = portal.post(f"/apps/{partner['id']}/keys", json={"name": "k"}, headers=dev.headers)
    assert r.json() == {"detail": "app_pending"}
    queue = admin.get(f"{shop['base']}/apps", params={"status": "pending"}).json()["items"]
    assert [(a["id"], a["developer_email"]) for a in queue] == [(partner["id"], dev.email)]
    r = admin.patch(f"{shop['base']}/apps/{partner['id']}", json={"status": "approved"})
    assert r.json()["status"] == "approved"

    # An app on Free: approved at once; a key; calls until the quota is used.
    app = portal.post(
        "/apps",
        json={"workspace": shop["slug"], "api_id": shop["orders"], "plan_id": shop["free"],
              "name": "My shop"},
        headers=dev.headers,
    ).json()  # fmt: skip
    assert app["status"] == "approved", app
    key = portal.post(f"/apps/{app['id']}/keys", json={"name": "laptop"}, headers=dev.headers)
    assert key.status_code == 201, key.text
    call = {"headers": {"Host": shop["host"], "X-API-Key": key.json()["key"]}}
    _until(
        "the new key works at the gateway",
        lambda: gateway.get("/orders", **call).status_code == 200,
        CONFIG_WAIT,
    )
    for _ in range(QUOTA - 1):
        assert gateway.get("/orders", **call).status_code == 200
    over = gateway.get("/orders", **call)
    assert over.status_code == 429
    assert over.json()["error"] == "quota_exceeded"

    # Their own usage: the requests, and no quota left.
    def used_up() -> dict[str, Any] | None:
        usage = portal.get(f"/apps/{app['id']}/usage", headers=dev.headers).json()
        return usage if usage["quota"]["used"] == QUOTA else None

    usage = _until("the usage shows the quota used", used_up)
    assert usage["quota"] == usage["quota"] | {"monthly_quota": QUOTA, "remaining": 0}
    assert usage["traffic"]["totals"]["requests"] == QUOTA + 1  # the refused one too

    # Revoking the key closes the gateway to it.
    r = portal.delete(f"/apps/{app['id']}/keys/{key.json()['id']}", headers=dev.headers)
    assert r.status_code == 204
    _until(
        "the revoked key is refused",
        lambda: gateway.get("/orders", **call).status_code == 401,
        CONFIG_WAIT,
    )

    # Another developer never sees these apps.
    other = new_developer()
    assert portal.get("/apps", headers=other.headers).json() == {"items": [], "total": 0}
    for url in (f"/apps/{app['id']}", f"/apps/{app['id']}/keys", f"/apps/{app['id']}/usage"):
        assert portal.get(url, headers=other.headers).status_code == 404

    # A deprecated API is marked and takes no new apps.
    admin.patch(f"{shop['base']}/apis/{shop['orders']}", json={"status": "deprecated"})
    assert (
        portal.get(f"{catalog}/apis", headers=dev.headers).json()["items"][0]["status"]
        == "deprecated"
    )
    r = portal.post(
        "/apps",
        json={"workspace": shop["slug"], "api_id": shop["orders"], "plan_id": shop["free"],
              "name": "Too late"},
        headers=dev.headers,
    )  # fmt: skip
    assert r.json() == {"detail": "api_deprecated"}

    # Every developer change is in the workspace's log, by the developer.
    events = admin.get(f"{shop['base']}/audit-events", params={"page_size": 100}).json()["items"]
    by_developer = sorted(e["action"] for e in events if e["actor_type"] == "developer")
    assert by_developer == ["app.create", "app.create", "key.create", "key.revoke"]
    assert {e["actor_email"] for e in events if e["actor_type"] == "developer"} == {dev.email}
