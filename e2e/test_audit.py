"""MT.3 stories: the audit log, through the real stack."""

import os
import uuid
from typing import Any

import httpx

from conftest import TENANT, World


def _find(log: list[dict[str, Any]], request_id: str) -> dict[str, Any]:
    return next(e for e in log if e["request_id"] == request_id)


def test_a_change_appears_in_its_workspace_log(world: World) -> None:
    # The client's request id ties the change to its entry.
    request_id = f"e2e-{uuid.uuid4().hex[:12]}"
    body = {"name": world.name("audited"), "upstream_url": "http://mock-backend:9000"}
    r = world.admin.post(f"{world.base}/services", json=body, headers={"X-Request-ID": request_id})
    assert r.status_code == 201, r.text
    world._services.append(r.json()["id"])  # cleaned up with the rest
    assert r.headers["X-Request-ID"] == request_id

    log = world.admin.get(f"{world.base}/audit-events", params={"action": "service.create"}).json()[
        "items"
    ]
    event = _find(log, request_id)

    assert event["target_id"] == r.json()["id"]
    assert event["changes"]["name"] == [None, body["name"]]
    # The Platform Admin is Owner of `default`: acting within their own role.
    assert event["actor_type"] == "user"
    assert event["actor_email"] == os.environ["PLATFORM_ADMIN_EMAIL"].lower()


def test_the_platform_admins_change_shows_in_the_tenants_log(
    owner: httpx.Client, admin: httpx.Client
) -> None:
    # The Platform Admin (not a member of e2e-tenant) changes something there;
    # the tenant's Owner sees it, marked as platform power (decision 11).
    request_id = f"e2e-{uuid.uuid4().hex[:12]}"
    consumers = f"/workspaces/{TENANT}/consumers"
    r = admin.post(
        consumers,
        json={"name": f"e2e-{uuid.uuid4().hex[:6]}", "email": "e2e@example.com"},
        headers={"X-Request-ID": request_id},
    )
    assert r.status_code == 201, r.text
    try:
        log = owner.get(
            f"/workspaces/{TENANT}/audit-events", params={"target_type": "consumer"}
        ).json()["items"]
        event = _find(log, request_id)
        assert event["actor_type"] == "platform_admin"
        assert event["actor_email"] == os.environ["PLATFORM_ADMIN_EMAIL"].lower()
    finally:
        admin.delete(f"{consumers}/{r.json()['id']}")
