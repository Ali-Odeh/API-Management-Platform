"""V2.2 stories: plans with monthly quotas, and abusive clients held back.

Each run makes its own workspace (a new slug, deleted at the end): the
per-IP limits key on the workspace and the client IP, and every request of
these tests comes from the e2e container's one IP, so blocking it here
leaves every other story alone. (That another IP still passes is covered by
the gateway's Redis tests: one container can't send from two addresses.)
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest

from conftest import MOCK_BACKEND_URL, PERSON_EMAIL, Session, wait_for


@dataclass
class Shop:
    slug: str

    @property
    def base(self) -> str:
        return f"/workspaces/{self.slug}"

    @property
    def host(self) -> str:
        return f"{self.slug}.localhost"


@pytest.fixture
def shop(admin: httpx.Client, person: Session) -> Iterator[Shop]:
    """A workspace with /orders (protected) and /catalog (public)."""
    slug = f"e2e-plans-{uuid.uuid4().hex[:6]}"
    r = admin.post("/workspaces", json={"name": slug, "slug": slug, "owner_email": PERSON_EMAIL})
    assert r.status_code == 201, r.text
    made = Shop(slug)
    try:
        service = admin.post(
            f"{made.base}/services", json={"name": "api", "targets": [{"url": MOCK_BACKEND_URL}]}
        ).json()
        for prefix, auth in (("/orders", True), ("/catalog", False)):
            r = admin.post(
                f"{made.base}/routes",
                json={
                    "service_id": service["id"],
                    "path_prefix": prefix,
                    "methods": ["GET"],
                    "auth_required": auth,
                },
            )
            assert r.status_code == 201, r.text
        yield made
    finally:
        r = admin.request("DELETE", made.base, json={"confirm": slug})
        assert r.status_code == 204, r.text


def _consumer_with_key(admin: httpx.Client, shop: Shop, name: str, plan_id: str) -> tuple[str, str]:
    consumer = admin.post(
        f"{shop.base}/consumers",
        json={"name": name, "email": "e2e@example.com", "plan_id": plan_id},
    ).json()
    key = admin.post(f"{shop.base}/consumers/{consumer['id']}/keys", json={"name": "k"}).json()
    return consumer["id"], key["key"]


def _order(gateway: httpx.Client, shop: Shop, key: str) -> httpx.Response:
    return gateway.get("/orders/1", headers={"Host": shop.host, "X-API-Key": key})


def test_free_runs_out_of_its_quota_while_pro_keeps_working(
    gateway: httpx.Client, admin: httpx.Client, shop: Shop
) -> None:
    free = admin.post(
        f"{shop.base}/plans", json={"name": "Free", "rate_limit_per_min": 60, "monthly_quota": 3}
    ).json()
    pro = admin.post(f"{shop.base}/plans", json={"name": "Pro", "rate_limit_per_min": 600}).json()
    free_id, free_key = _consumer_with_key(admin, shop, "free-app", free["id"])
    _, pro_key = _consumer_with_key(admin, shop, "pro-app", pro["id"])
    first = wait_for(lambda: _order(gateway, shop, free_key), 200)
    assert first.headers["X-Quota-Limit"] == "3"

    answers = [_order(gateway, shop, free_key) for _ in range(3)]

    assert [a.status_code for a in answers] == [200, 200, 429]
    assert answers[1].headers["X-Quota-Remaining"] == "0"
    assert answers[2].json() == {"error": "quota_exceeded"}
    assert int(answers[2].headers["Retry-After"]) > 0  # until the month ends
    pro_answer = wait_for(lambda: _order(gateway, shop, pro_key), 200)
    assert pro_answer.status_code == 200 and "X-Quota-Limit" not in pro_answer.headers

    # Moving the consumer to Pro applies with the next config update.
    r = admin.patch(f"{shop.base}/consumers/{free_id}", json={"plan_id": pro["id"]})
    assert r.status_code == 200, r.text
    assert wait_for(lambda: _order(gateway, shop, free_key), 200).status_code == 200


def test_a_plan_above_the_workspaces_limit_is_refused(admin: httpx.Client, shop: Shop) -> None:
    ceiling = admin.get(shop.base).json()["limits"]["rate_limit_per_min"]
    r = admin.post(
        f"{shop.base}/plans", json={"name": "Too big", "rate_limit_per_min": ceiling + 1}
    )
    assert r.status_code == 422


def test_one_ip_hammering_a_public_route_is_limited(gateway: httpx.Client, shop: Shop) -> None:
    first = wait_for(lambda: gateway.get("/catalog", headers={"Host": shop.host}), 200)
    limit = int(first.headers["X-RateLimit-Limit"])  # this IP's bucket (default 600/min)

    # The bucket refills while we send, so send until it refuses.
    allowed = 1
    for _ in range(2 * limit):
        r = gateway.get("/catalog", headers={"Host": shop.host})
        if r.status_code == 429:
            break
        allowed += 1

    assert (r.status_code, r.json()) == (429, {"error": "rate_limit_exceeded"})
    assert allowed >= limit  # the whole bucket was there first


def test_repeated_wrong_keys_get_the_ip_refused(
    gateway: httpx.Client, admin: httpx.Client, shop: Shop
) -> None:
    default_plan = admin.get(f"{shop.base}/plans").json()["items"][0]["id"]
    _, good_key = _consumer_with_key(admin, shop, "real-app", default_plan)
    assert wait_for(lambda: _order(gateway, shop, good_key), 200).status_code == 200

    answers = [_order(gateway, shop, f"ak_live_guess{i:027d}").status_code for i in range(25)]

    # The default limit is 20 wrong keys in 5 minutes.
    assert answers[:20] == [401] * 20
    assert set(answers[20:]) == {429}
    refused = _order(gateway, shop, good_key)  # even a valid key, from this IP
    assert (refused.status_code, refused.json()) == (429, {"error": "too_many_failed_attempts"})
    assert 0 < int(refused.headers["Retry-After"]) <= 300
