"""End to end: one admin's day, checked through the gateway's eyes.

Every other test checks one piece. This one walks the whole MVP admin flow
through the real API and, after each step, looks at /internal/config exactly
as the Go gateway will in milestone M3.
"""

from typing import Any

from httpx import AsyncClient

from app.auth.models import Admin
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

API = "/api/v1"
CONFIG = "/internal/config"


async def test_admin_flow_as_seen_by_the_gateway(gateway: AsyncClient, admin: Admin) -> None:
    async def config() -> dict[str, Any]:
        r = await gateway.get(CONFIG)
        assert r.status_code == 200
        body: dict[str, Any] = r.json()
        assert r.headers["ETag"] == f'"{body["version"]}"'
        return body

    # 0. Nothing configured yet.
    start = await config()
    assert start["routes"] == [] and start["keys"] == []

    # 1. Log in.
    login = await gateway.post(
        f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    admin = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # 2. Register the backend and route /v1/orders to it.
    service = (
        await gateway.post(
            f"{API}/services",
            json={
                "name": "orders",
                "upstream_url": "http://mock-backend:9000/",
                "timeout_ms": 2000,
            },
            headers=admin,
        )
    ).json()
    route = (
        await gateway.post(
            f"{API}/routes",
            json={
                "service_id": service["id"],
                "path_prefix": "/v1/orders/",
                "methods": ["get", "POST"],
            },
            headers=admin,
        )
    ).json()

    after_route = await config()
    assert after_route["version"] > start["version"]
    assert after_route["routes"] == [
        {
            "id": route["id"],
            "path_prefix": "/v1/orders",  # cleaned up by the API
            "methods": ["GET", "POST"],
            "strip_prefix": False,
            "auth_required": True,  # secure by default
            "upstream_url": "http://mock-backend:9000",
            "timeout_ms": 2000,
        }
    ]

    # 3. A consumer app gets a key. The gateway sees only the key's hash.
    consumer = (
        await gateway.post(
            f"{API}/consumers",
            json={"name": "mobile-app", "email": "dev@example.com", "rate_limit_per_min": 120},
            headers=admin,
        )
    ).json()
    key = (
        await gateway.post(
            f"{API}/consumers/{consumer['id']}/keys", json={"name": "ios"}, headers=admin
        )
    ).json()

    after_key = await config()
    assert len(after_key["keys"]) == 1
    seen = after_key["keys"][0]
    assert seen["id"] == key["id"]
    assert seen["consumer_status"] == "active"
    assert seen["rate_limit_per_min"] == 120
    assert key["key"] not in str(after_key)  # never the raw key

    # 4. Block the consumer: the key stays, marked blocked (gateway -> 403).
    await gateway.patch(
        f"{API}/consumers/{consumer['id']}", json={"status": "blocked"}, headers=admin
    )
    assert (await config())["keys"][0]["consumer_status"] == "blocked"

    # 5. Revoke the key: it disappears (gateway -> 401).
    await gateway.delete(f"{API}/keys/{key['id']}", headers=admin)
    assert (await config())["keys"] == []

    # 6. Switch the route off: it disappears (gateway -> 404).
    await gateway.patch(f"{API}/routes/{route['id']}", json={"is_active": False}, headers=admin)
    assert (await config())["routes"] == []

    # 7. Clean up everything: back to an empty config, at a higher version.
    await gateway.delete(f"{API}/routes/{route['id']}", headers=admin)
    assert (
        await gateway.delete(f"{API}/services/{service['id']}", headers=admin)
    ).status_code == 204
    await gateway.delete(f"{API}/consumers/{consumer['id']}", headers=admin)

    end = await config()
    assert end["routes"] == [] and end["keys"] == []
    assert end["version"] > after_key["version"]

    # 8. And an up-to-date gateway gets 304.
    same = await gateway.get(CONFIG, headers={"If-None-Match": f'"{end["version"]}"'})
    assert same.status_code == 304
