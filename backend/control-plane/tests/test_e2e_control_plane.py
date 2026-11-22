"""End to end: one admin's day, checked through the gateway's eyes.

Every other test checks one piece. This one walks the whole MVP admin flow
through the real API and, after each step, looks at /internal/config exactly
as the Go gateway does (its `default` workspace, from a full answer).
"""

from typing import Any

from httpx import AsyncClient

API = "/api/v1"
CONFIG = "/internal/config"


async def test_admin_flow_as_seen_by_the_gateway(
    gateway: AsyncClient, auth_headers: dict[str, str]
) -> None:
    async def config() -> dict[str, Any]:
        r = await gateway.get(CONFIG)
        assert r.status_code == 200
        body: dict[str, Any] = r.json()
        assert r.headers["ETag"] == f'"{body["seq"]}"'
        assert body["full"] is True
        default: dict[str, Any] = next(w for w in body["workspaces"] if w["slug"] == "default")
        return {**default, "seq": body["seq"]}

    # 0. Nothing configured yet.
    start = await config()
    assert start["routes"] == [] and start["keys"] == []

    # 1. Log in (with GoTrue; here a token signed like GoTrue's).
    admin = auth_headers

    # 2. Register the backend and route /v1/orders to it.
    service = (
        await gateway.post(
            f"{API}/workspaces/default/services",
            json={
                "name": "orders",
                "targets": [{"url": "http://mock-backend:9000/"}],
                "timeout_ms": 2000,
            },
            headers=admin,
        )
    ).json()
    route = (
        await gateway.post(
            f"{API}/workspaces/default/routes",
            json={
                "service_id": service["id"],
                "path_prefix": "/v1/orders/",
                "methods": ["get", "POST"],
            },
            headers=admin,
        )
    ).json()

    after_route = await config()
    assert after_route["seq"] > start["seq"]
    assert after_route["version"] > start["version"]
    assert after_route["services"] == [
        {
            "id": service["id"],
            "timeout_ms": 2000,
            "lb_algorithm": "round_robin",
            "health": {
                "path": "/health",
                "interval_s": 10,
                "unhealthy": 3,
                "healthy": 2,
                "passive_failures": 5,
                "passive_eject_s": 30,
            },
            # The URL cleaned up by the API.
            "targets": [
                {
                    "id": service["targets"][0]["id"],
                    "url": "http://mock-backend:9000",
                    "weight": 100,
                }
            ],
        }
    ]
    assert after_route["routes"] == [
        {
            "id": route["id"],
            "service_id": service["id"],
            "path_prefix": "/v1/orders",  # cleaned up by the API
            "methods": ["GET", "POST"],
            "strip_prefix": False,
            "auth_required": True,  # secure by default
            "max_body_bytes": 10 * 1024**2,  # the platform default (MAX_REQUEST_BODY)
            "cors": {
                "origins": [],  # CORS off
                "methods": ["GET", "POST"],  # the route's own methods
                "headers": [],
                "credentials": False,
                "max_age": 600,
            },
        }
    ]

    # 3. A consumer app on a plan gets a key. The gateway sees only the key's
    # hash, with its plan's limits (V2.2).
    plan = (
        await gateway.post(
            f"{API}/workspaces/default/plans",
            json={"name": "Gold", "rate_limit_per_min": 120, "monthly_quota": 5000},
            headers=admin,
        )
    ).json()
    consumer = (
        await gateway.post(
            f"{API}/workspaces/default/consumers",
            json={"name": "mobile-app", "email": "dev@example.com", "plan_id": plan["id"]},
            headers=admin,
        )
    ).json()
    key = (
        await gateway.post(
            f"{API}/workspaces/default/consumers/{consumer['id']}/keys",
            json={"name": "ios"},
            headers=admin,
        )
    ).json()

    after_key = await config()
    assert len(after_key["keys"]) == 1
    seen = after_key["keys"][0]
    assert seen["id"] == key["id"]
    assert seen["consumer_status"] == "active"
    assert (seen["plan_id"], seen["rate_limit_per_min"], seen["monthly_quota"]) == (
        plan["id"],
        120,
        5000,
    )
    assert key["key"] not in str(after_key)  # never the raw key

    # 4. Block the consumer: the key stays, marked blocked (gateway -> 403).
    await gateway.patch(
        f"{API}/workspaces/default/consumers/{consumer['id']}",
        json={"status": "blocked"},
        headers=admin,
    )
    assert (await config())["keys"][0]["consumer_status"] == "blocked"

    # 5. Revoke the key: it disappears (gateway -> 401).
    await gateway.delete(f"{API}/workspaces/default/keys/{key['id']}", headers=admin)
    assert (await config())["keys"] == []

    # 6. Switch the route off: it disappears (gateway -> 404).
    await gateway.patch(
        f"{API}/workspaces/default/routes/{route['id']}", json={"is_active": False}, headers=admin
    )
    assert (await config())["routes"] == []

    # 7. Clean up everything: back to an empty config, at a higher version.
    await gateway.delete(f"{API}/workspaces/default/routes/{route['id']}", headers=admin)
    assert (
        await gateway.delete(f"{API}/workspaces/default/services/{service['id']}", headers=admin)
    ).status_code == 204
    await gateway.delete(f"{API}/workspaces/default/consumers/{consumer['id']}", headers=admin)

    end = await config()
    assert end["routes"] == [] and end["keys"] == []
    assert end["seq"] > after_key["seq"]

    # 8. And an up-to-date gateway gets 304.
    same = await gateway.get(CONFIG, params={"since": end["seq"]})
    assert same.status_code == 304
