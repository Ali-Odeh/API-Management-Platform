"""Apps (P.2): developers make apps on offered plans, members approve them, keys and usage.

Which tokens open which endpoints, isolation between developers and the
audit of every portal write are in test_portal_guards.py.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import pool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.apis.models import ApiStatus
from app.apps.crud import consumer_status
from app.apps.models import MAX_APPS_PER_DEVELOPER, AppStatus
from app.audit.models import AuditEvent
from app.consumers.models import Consumer, ConsumerStatus
from app.core.db import SessionLocal
from app.developers.models import DeveloperStatus
from app.workspaces.models import WorkspaceStatus
from tests.conftest import OWNER_URL
from tests.factories import (
    make_api,
    make_developer,
    make_plan,
    make_route,
    make_service,
    make_workspace,
)
from tests.fakes import bearer, make_developer_token
from tests.test_api_guards import set_status
from tests.test_gateway_config import Watch

Headers = dict[str, str]
APPS = "/api/v1/portal/apps"


@dataclass
class Shop:
    workspace_id: uuid.UUID
    api: uuid.UUID  # Orders, published
    old_api: uuid.UUID  # Legacy, deprecated
    draft: uuid.UUID
    free: uuid.UUID  # offered, no approval
    partner: uuid.UUID  # offered, requires approval
    hidden: uuid.UUID  # not offered to developers
    developer: uuid.UUID
    dev: Headers


@pytest.fixture
async def shop(db: AsyncSession) -> Shop:
    workspace = await make_workspace(db, slug="shop")
    service = await make_service(db, workspace.id)
    route = await make_route(db, workspace.id, service.id)
    api = await make_api(db, workspace.id, status=ApiStatus.PUBLISHED, route_ids=(route.id,))
    old = await make_api(db, workspace.id, slug="legacy", status=ApiStatus.DEPRECATED)
    draft = await make_api(db, workspace.id, slug="labs")
    free = await make_plan(
        db,
        workspace.id,
        name="Free",
        rate_limit_per_min=60,
        monthly_quota=1000,
        portal_visible=True,
    )
    partner = await make_plan(
        db, workspace.id, name="Partner", portal_visible=True, requires_approval=True
    )
    hidden = await make_plan(db, workspace.id, name="Internal")
    developer = await make_developer(db)
    return Shop(
        workspace_id=workspace.id,
        api=api.id,
        old_api=old.id,
        draft=draft.id,
        free=free.id,
        partner=partner.id,
        hidden=hidden.id,
        developer=developer.id,
        dev=bearer(make_developer_token(developer.auth_user_id, developer.email)),
    )


async def create(client: AsyncClient, shop: Shop, **overrides: Any) -> Response:
    body = {
        "workspace": "shop",
        "api_id": str(shop.api),
        "plan_id": str(shop.free),
        "name": "My app",
    } | {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in overrides.items()}
    return await client.post(APPS, json=body, headers=shop.dev)


async def consumer_of(app_id: str) -> Consumer:
    async with SessionLocal() as session:
        row = await session.execute(
            text("SELECT consumer_id FROM apps WHERE id = :id"), {"id": uuid.UUID(app_id)}
        )
        return await session.get_one(Consumer, row.scalar_one())


async def events(action: str) -> list[AuditEvent]:
    async with SessionLocal() as session:
        return list(await session.scalars(select(AuditEvent).where(AuditEvent.action == action)))


# ---- What a developer can choose from


async def test_a_developer_sees_the_plans_offered_to_developers(
    client: AsyncClient, shop: Shop
) -> None:
    r = await client.get("/api/v1/portal/workspaces/shop/plans", headers=shop.dev)
    assert r.status_code == 200, r.text
    assert [(p["name"], p["requires_approval"]) for p in r.json()["items"]] == [
        ("Free", False),
        ("Partner", True),
    ]
    assert set(r.json()["items"][0]) == {
        "id", "name", "rate_limit_per_min", "monthly_quota", "requires_approval",
    }  # fmt: skip


async def test_no_plans_for_a_workspace_without_a_catalog(client: AsyncClient, shop: Shop) -> None:
    await set_status(shop.workspace_id, WorkspaceStatus.SUSPENDED)
    r = await client.get("/api/v1/portal/workspaces/shop/plans", headers=shop.dev)
    assert r.status_code == 404


# ---- Making an app


async def test_an_app_on_a_plain_plan_is_approved_with_an_active_consumer(
    client: AsyncClient, shop: Shop
) -> None:
    r = await create(client, shop)

    assert r.status_code == 201, r.text
    app = r.json()
    assert app["status"] == "approved"
    assert app["workspace"] == {"slug": "shop", "name": "Shop"}
    assert (app["api"]["slug"], app["plan"]["name"]) == ("orders", "Free")
    consumer = await consumer_of(app["id"])
    assert consumer.status == ConsumerStatus.ACTIVE
    assert (consumer.name, consumer.email, consumer.plan_id) == (
        f"app-{app['id']}",
        "dev@portal.test",
        shop.free,
    )
    [event] = await events("app.create")
    assert (event.workspace_id, event.actor_type) == (shop.workspace_id, "developer")
    assert event.actor_user_id == shop.developer
    assert event.changes["status"] == [None, "approved"]
    assert event.changes["plan_id"] == [None, str(shop.free)]


async def test_an_app_on_a_plan_that_needs_approval_waits_blocked(
    client: AsyncClient, shop: Shop
) -> None:
    r = await create(client, shop, plan_id=shop.partner)
    assert r.json()["status"] == "pending"
    assert (await consumer_of(r.json()["id"])).status == ConsumerStatus.BLOCKED


async def test_making_an_app_bumps_the_config(client: AsyncClient, shop: Shop) -> None:
    watch = await Watch().start()
    await create(client, shop)
    await watch.assert_bumped(shop.workspace_id)


@pytest.mark.parametrize(
    ("field", "value", "detail"),
    [
        ("workspace", "nowhere", "Workspace not found"),
        ("api_id", "draft", "API not found"),
        ("api_id", "random", "API not found"),
        ("plan_id", "hidden", "Plan not found"),
        ("plan_id", "random", "Plan not found"),
    ],
)
async def test_what_is_not_offered_is_422(
    client: AsyncClient, shop: Shop, field: str, value: str, detail: str
) -> None:
    resolved = {"draft": shop.draft, "hidden": shop.hidden, "random": uuid.uuid4()}.get(
        value, value
    )
    r = await create(client, shop, **{field: resolved})
    assert r.status_code == 422
    assert r.json() == {"detail": detail}


async def test_another_workspaces_plan_or_api_is_422(
    client: AsyncClient, shop: Shop, db: AsyncSession
) -> None:
    other = await make_workspace(db, slug="other")
    their_api = await make_api(db, other.id, status=ApiStatus.PUBLISHED)
    their_plan = await make_plan(db, other.id, portal_visible=True)
    assert (await create(client, shop, api_id=their_api.id)).status_code == 422
    assert (await create(client, shop, plan_id=their_plan.id)).status_code == 422


async def test_a_deprecated_api_takes_no_new_apps(client: AsyncClient, shop: Shop) -> None:
    r = await create(client, shop, api_id=shop.old_api)
    assert r.status_code == 409
    assert r.json() == {"detail": "api_deprecated"}


@pytest.mark.parametrize("status", [WorkspaceStatus.SUSPENDED, WorkspaceStatus.DELETED])
async def test_no_new_apps_in_a_workspace_that_isnt_active(
    client: AsyncClient, shop: Shop, status: WorkspaceStatus
) -> None:
    await set_status(shop.workspace_id, status)
    r = await create(client, shop)
    assert r.status_code == 422
    assert r.json() == {"detail": "Workspace not found"}


async def test_a_name_is_unique_per_developer_in_a_workspace(
    client: AsyncClient, shop: Shop, db: AsyncSession
) -> None:
    assert (await create(client, shop)).status_code == 201
    r = await create(client, shop)
    assert r.status_code == 409
    # Another developer may use it.
    other = await make_developer(db, email="other@portal.test")
    headers = bearer(make_developer_token(other.auth_user_id, other.email))
    body = {
        "workspace": "shop",
        "api_id": str(shop.api),
        "plan_id": str(shop.free),
        "name": "My app",
    }
    assert (await client.post(APPS, json=body, headers=headers)).status_code == 201


async def test_a_developer_has_at_most_ten_apps_per_workspace(
    client: AsyncClient, shop: Shop
) -> None:
    for i in range(MAX_APPS_PER_DEVELOPER):
        assert (await create(client, shop, name=f"app {i}")).status_code == 201
    r = await create(client, shop, name="one more")
    assert r.status_code == 409
    assert r.json() == {"detail": "quota_exceeded"}


async def test_apps_count_against_the_workspaces_consumers(
    client: AsyncClient, shop: Shop, db: AsyncSession
) -> None:
    await db.execute(text("UPDATE workspaces SET max_consumers = 1 WHERE slug = 'shop'"))
    await db.commit()
    assert (await create(client, shop)).status_code == 201
    r = await create(client, shop, name="second")
    assert r.status_code == 409
    assert r.json() == {"detail": "quota_exceeded"}


# ---- A developer's own apps


async def test_my_apps_newest_first(client: AsyncClient, shop: Shop) -> None:
    first = (await create(client, shop, name="first")).json()
    second = (await create(client, shop, name="second", plan_id=shop.partner)).json()

    r = await client.get(APPS, headers=shop.dev)

    assert [a["id"] for a in r.json()["items"]] == [second["id"], first["id"]]
    assert (await client.get(f"{APPS}/{first['id']}", headers=shop.dev)).json() == first


async def test_apps_in_a_deleted_workspace_are_gone(client: AsyncClient, shop: Shop) -> None:
    app = (await create(client, shop)).json()
    await set_status(shop.workspace_id, WorkspaceStatus.DELETED)
    assert (await client.get(APPS, headers=shop.dev)).json()["total"] == 0
    assert (await client.get(f"{APPS}/{app['id']}", headers=shop.dev)).status_code == 404


async def test_deleting_an_app_deletes_its_consumer_and_keys(
    client: AsyncClient, shop: Shop, db: AsyncSession
) -> None:
    app = (await create(client, shop)).json()
    await client.post(f"{APPS}/{app['id']}/keys", json={"name": "k"}, headers=shop.dev)
    consumer = await consumer_of(app["id"])

    r = await client.delete(f"{APPS}/{app['id']}", headers=shop.dev)

    assert r.status_code == 204
    assert await db.get(Consumer, consumer.id) is None
    assert (await client.get(f"{APPS}/{app['id']}", headers=shop.dev)).status_code == 404
    [event] = await events("app.delete")
    assert event.changes["name"] == ["My app", None]


async def test_a_suspended_workspace_is_read_only_for_developers(
    client: AsyncClient, shop: Shop
) -> None:
    app = (await create(client, shop)).json()
    await set_status(shop.workspace_id, WorkspaceStatus.SUSPENDED)

    assert (await client.get(f"{APPS}/{app['id']}", headers=shop.dev)).status_code == 200
    for method, url, body in (
        ("POST", f"{APPS}/{app['id']}/keys", {"name": "k"}),
        ("DELETE", f"{APPS}/{app['id']}/keys/{uuid.uuid4()}", None),
        ("DELETE", f"{APPS}/{app['id']}", None),
    ):
        r = await client.request(method, url, json=body, headers=shop.dev)
        assert r.status_code == 403, (method, url)
        assert r.json() == {"detail": "workspace_suspended"}


# ---- Keys


async def test_an_approved_app_gets_keys_with_the_admin_rules(
    client: AsyncClient, shop: Shop
) -> None:
    app = (await create(client, shop)).json()
    url = f"{APPS}/{app['id']}/keys"

    r = await client.post(url, json={"name": "laptop"}, headers=shop.dev)

    assert r.status_code == 201, r.text
    assert r.headers["Cache-Control"] == "no-store"
    assert r.json()["key"].startswith("ak_live_")
    listed = (await client.get(url, headers=shop.dev)).json()
    assert [k["key_prefix"] for k in listed["items"]] == [r.json()["key_prefix"]]
    assert "key" not in listed["items"][0]
    for i in range(9):
        assert (await client.post(url, json={"name": f"k{i}"}, headers=shop.dev)).is_success
    eleventh = await client.post(url, json={"name": "one too many"}, headers=shop.dev)
    assert eleventh.status_code == 409


@pytest.mark.parametrize(
    ("status", "detail"), [("pending", "app_pending"), ("rejected", "app_rejected")]
)
async def test_an_app_that_isnt_approved_gets_no_keys(
    client: AsyncClient, shop: Shop, db: AsyncSession, status: str, detail: str
) -> None:
    app = (await create(client, shop, plan_id=shop.partner)).json()
    await db.execute(
        text("UPDATE apps SET status = :s WHERE id = :id"),
        {"s": status, "id": uuid.UUID(app["id"])},
    )
    await db.commit()
    r = await client.post(f"{APPS}/{app['id']}/keys", json={"name": "k"}, headers=shop.dev)
    assert r.status_code == 409
    assert r.json() == {"detail": detail}


async def test_revoking_a_key(client: AsyncClient, shop: Shop) -> None:
    app = (await create(client, shop)).json()
    key = (
        await client.post(f"{APPS}/{app['id']}/keys", json={"name": "k"}, headers=shop.dev)
    ).json()

    for _ in range(2):  # revoking again changes nothing
        r = await client.delete(f"{APPS}/{app['id']}/keys/{key['id']}", headers=shop.dev)
        assert r.status_code == 204
    listed = (await client.get(f"{APPS}/{app['id']}/keys", headers=shop.dev)).json()["items"]
    assert listed[0]["is_active"] is False
    [event] = await events("key.revoke")
    assert (event.actor_type, event.workspace_id) == ("developer", shop.workspace_id)


async def test_a_key_of_another_app_is_not_found_through_this_one(
    client: AsyncClient, shop: Shop
) -> None:
    mine = (await create(client, shop, name="one")).json()
    other = (await create(client, shop, name="two")).json()
    key = (
        await client.post(f"{APPS}/{other['id']}/keys", json={"name": "k"}, headers=shop.dev)
    ).json()
    r = await client.delete(f"{APPS}/{mine['id']}/keys/{key['id']}", headers=shop.dev)
    assert r.status_code == 404
    assert r.json() == {"detail": "API key not found"}


# ---- Members decide: the approval queue


async def test_an_api_admin_approves_and_the_consumer_follows(
    client: AsyncClient, shop: Shop, mfa_headers: Headers, db: AsyncSession
) -> None:
    pending = (await create(client, shop, plan_id=shop.partner)).json()
    queue = await client.get("/api/v1/workspaces/shop/apps?status=pending", headers=mfa_headers)
    assert [a["id"] for a in queue.json()["items"]] == [pending["id"]]
    assert queue.json()["items"][0]["developer_email"] == "dev@portal.test"

    watch = await Watch().start()
    url = f"/api/v1/workspaces/shop/apps/{pending['id']}"
    r = await client.patch(url, json={"status": "approved"}, headers=mfa_headers)

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert (await consumer_of(pending["id"])).status == ConsumerStatus.ACTIVE
    await watch.assert_bumped(shop.workspace_id)
    [event] = await events("app.update")
    assert event.changes == {"status": ["pending", "approved"]}

    # Rejected later: its keys stop working.
    r = await client.patch(url, json={"status": "rejected"}, headers=mfa_headers)
    assert (await consumer_of(pending["id"])).status == ConsumerStatus.BLOCKED
    # The same decision again changes nothing.
    await client.patch(url, json={"status": "rejected"}, headers=mfa_headers)
    assert len(await events("app.update")) == 2


async def test_an_app_cannot_be_put_back_to_pending(
    client: AsyncClient, shop: Shop, mfa_headers: Headers
) -> None:
    app = (await create(client, shop)).json()
    r = await client.patch(
        f"/api/v1/workspaces/shop/apps/{app['id']}", json={"status": "pending"}, headers=mfa_headers
    )
    assert r.status_code == 422


async def test_an_api_with_apps_cannot_be_deleted(
    client: AsyncClient, shop: Shop, mfa_headers: Headers
) -> None:
    await create(client, shop)
    r = await client.delete(f"/api/v1/workspaces/shop/apis/{shop.api}", headers=mfa_headers)
    assert r.status_code == 409
    assert r.json() == {"detail": "api_in_use"}


async def test_deleting_the_consumer_deletes_its_app(
    client: AsyncClient, shop: Shop, mfa_headers: Headers
) -> None:
    app = (await create(client, shop)).json()
    consumer = await consumer_of(app["id"])
    r = await client.delete(f"/api/v1/workspaces/shop/consumers/{consumer.id}", headers=mfa_headers)
    assert r.status_code == 204
    assert (await client.get(f"{APPS}/{app['id']}", headers=shop.dev)).status_code == 404


# ---- A blocked developer's apps stop working everywhere


async def test_blocking_a_developer_blocks_their_approved_apps(
    client: AsyncClient, shop: Shop, mfa_headers: Headers
) -> None:
    approved = (await create(client, shop, name="live")).json()
    pending = (await create(client, shop, name="waiting", plan_id=shop.partner)).json()
    url = f"/api/v1/developers/{shop.developer}"

    await client.patch(url, json={"status": "blocked"}, headers=mfa_headers)
    assert (await consumer_of(approved["id"])).status == ConsumerStatus.BLOCKED
    # Approving while the developer is blocked keeps the consumer blocked.
    await client.patch(
        f"/api/v1/workspaces/shop/apps/{pending['id']}",
        json={"status": "approved"},
        headers=mfa_headers,
    )
    assert (await consumer_of(pending["id"])).status == ConsumerStatus.BLOCKED

    await client.patch(url, json={"status": "active"}, headers=mfa_headers)
    assert (await consumer_of(approved["id"])).status == ConsumerStatus.ACTIVE
    assert (await consumer_of(pending["id"])).status == ConsumerStatus.ACTIVE


@pytest.mark.parametrize("app_status", list(AppStatus))
@pytest.mark.parametrize("developer_status", list(DeveloperStatus))
def test_the_consumer_rule(app_status: AppStatus, developer_status: DeveloperStatus) -> None:
    active = app_status == AppStatus.APPROVED and developer_status == DeveloperStatus.ACTIVE
    expected = ConsumerStatus.ACTIVE if active else ConsumerStatus.BLOCKED
    assert consumer_status(app_status, developer_status) == expected


# ---- Usage and quota


async def _log(
    workspace_id: uuid.UUID, consumer_id: uuid.UUID, rows: list[tuple[datetime, int, str]]
) -> None:
    """Request log rows, as the usage-ingest worker writes them."""
    owner = create_async_engine(OWNER_URL, poolclass=pool.NullPool)
    async with owner.begin() as conn:
        await conn.execute(
            text("SELECT ensure_request_log_partitions(:months)"),
            {"months": sorted({ts.date().replace(day=1) for ts, _, _ in rows})},
        )
        for ts, status, ratelimit in rows:
            await conn.execute(
                text(
                    "INSERT INTO request_logs (event_id, ts, workspace_id, consumer_id, method, "
                    "path, status, latency_ms, upstream_ms, bytes, ratelimit) VALUES "
                    "(gen_random_uuid(), :ts, :ws, :c, 'GET', '/v1/orders', :s, 1, 0, 0, :r)"
                ),
                {"ts": ts, "ws": workspace_id, "c": consumer_id, "s": status, "r": ratelimit},
            )
    await owner.dispose()


async def test_usage_shows_the_apps_traffic_and_quota(client: AsyncClient, shop: Shop) -> None:
    app = (await create(client, shop)).json()
    consumer = await consumer_of(app["id"])
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    await _log(
        shop.workspace_id,
        consumer.id,
        [
            (now - timedelta(minutes=5), 200, "ok"),
            (now - timedelta(minutes=4), 200, "ok"),
            (now - timedelta(minutes=3), 429, "limited_quota"),  # refused: spends nothing
            (month_start - timedelta(days=1, minutes=1), 200, "ok"),  # last month, over a day ago
        ],
    )

    r = await client.get(f"{APPS}/{app['id']}/usage?interval=hour", headers=shop.dev)

    assert r.status_code == 200, r.text
    usage = r.json()
    assert usage["traffic"]["totals"]["requests"] == 3  # the last day: last month's row is older
    assert usage["quota"]["monthly_quota"] == 1000
    assert usage["quota"]["used"] == 2
    assert usage["quota"]["remaining"] == 998
    resets = datetime.fromisoformat(usage["quota"]["resets_at"])
    assert resets == (month_start + timedelta(days=32)).replace(day=1)


async def test_usage_without_a_quota(client: AsyncClient, shop: Shop) -> None:
    app = (await create(client, shop, plan_id=shop.partner)).json()
    r = await client.get(f"{APPS}/{app['id']}/usage", headers=shop.dev)
    assert r.json()["quota"]["monthly_quota"] is None
    assert r.json()["quota"]["remaining"] is None
    assert r.json()["traffic"]["totals"]["requests"] == 0
