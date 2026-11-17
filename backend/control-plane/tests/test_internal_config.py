"""The gateway's config: the snapshot and the internal token check."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import engine
from app.core.deps import InternalOnly
from app.gateway_config.crud import read_snapshot, read_version
from app.workspaces.models import Workspace
from tests.factories import make_api_key, make_consumer, make_route, make_service

# =====================================================================
# The snapshot
# =====================================================================


async def test_empty_snapshot() -> None:
    snapshot = await read_snapshot(engine)
    assert snapshot.version == 1
    assert snapshot.routes == []
    assert snapshot.keys == []


async def test_snapshot_joins_routes_with_their_service(
    db: AsyncSession, workspace: Workspace
) -> None:
    orders = await make_service(
        db, workspace.id, name="orders", upstream_url="http://orders:9000", timeout_ms=3000
    )
    route = await make_route(
        db,
        workspace.id,
        orders.id,
        path_prefix="/v1/orders",
        methods=["GET", "POST"],
        strip_prefix=True,
        auth_required=False,
    )

    snapshot = await read_snapshot(engine)
    assert [r.model_dump() for r in snapshot.routes] == [
        {
            "id": route.id,
            "path_prefix": "/v1/orders",
            "methods": ["GET", "POST"],
            "strip_prefix": True,
            "auth_required": False,
            "upstream_url": "http://orders:9000",
            "timeout_ms": 3000,
        }
    ]


async def test_snapshot_leaves_out_what_is_switched_off(
    db: AsyncSession, workspace: Workspace
) -> None:
    live = await make_service(db, workspace.id, name="live")
    off = await make_service(db, workspace.id, name="off", is_active=False)
    await make_route(db, workspace.id, live.id, path_prefix="/b-live")
    await make_route(db, workspace.id, live.id, path_prefix="/a-route-off", is_active=False)
    await make_route(db, workspace.id, off.id, path_prefix="/c-service-off")

    snapshot = await read_snapshot(engine)
    assert [r.path_prefix for r in snapshot.routes] == ["/b-live"]


async def test_snapshot_routes_are_sorted_by_prefix(db: AsyncSession, workspace: Workspace) -> None:
    service = await make_service(db, workspace.id)
    for prefix in ["/v2/orders", "/", "/v1/orders"]:
        await make_route(db, workspace.id, service.id, path_prefix=prefix)
    snapshot = await read_snapshot(engine)
    assert [r.path_prefix for r in snapshot.routes] == ["/", "/v1/orders", "/v2/orders"]


async def test_snapshot_keys(db: AsyncSession, workspace: Workspace) -> None:
    active = await make_consumer(db, workspace.id, name="active-app", rate_limit_per_min=120)
    blocked = await make_consumer(db, workspace.id, name="blocked-app", status="blocked")
    good = await make_api_key(db, workspace.id, active.id)
    revoked = await make_api_key(db, workspace.id, active.id)
    revoked.revoked_at = good.created_at
    await db.commit()
    blocked_key = await make_api_key(db, workspace.id, blocked.id)

    snapshot = await read_snapshot(engine)
    by_id = {k.id: k for k in snapshot.keys}

    # Revoked keys are left out entirely: the gateway answers 401 for them.
    assert set(by_id) == {good.id, blocked_key.id}
    assert by_id[good.id].key_hash == good.key_hash
    assert by_id[good.id].consumer_status == "active"
    assert by_id[good.id].rate_limit_per_min == 120
    # Blocked consumers' keys stay in, marked, so the gateway can answer 403.
    assert by_id[blocked_key.id].consumer_status == "blocked"
    # Sorted by id, so the same version always gives the same JSON.
    assert [k.id for k in snapshot.keys] == sorted(by_id)


async def test_snapshot_version_follows_changes(db: AsyncSession, workspace: Workspace) -> None:
    await make_consumer(db, workspace.id)
    assert (await read_snapshot(engine)).version == await read_version(engine) == 2


async def test_snapshot_covers_every_workspace(
    db: AsyncSession, workspace: Workspace, other_workspace: Workspace
) -> None:
    # MVP: one global snapshot. The gateway serves all tenants.
    ours = await make_service(db, workspace.id, name="ours")
    theirs = await make_service(db, other_workspace.id, name="theirs")
    await make_route(db, workspace.id, ours.id, path_prefix="/ours")
    await make_route(db, other_workspace.id, theirs.id, path_prefix="/theirs")
    snapshot = await read_snapshot(engine)
    assert {r.path_prefix for r in snapshot.routes} == {"/ours", "/theirs"}


# =====================================================================
# The internal token check
# =====================================================================


@pytest.fixture
async def internal_client() -> AsyncClient:
    mini = FastAPI()

    @mini.get("/internal/ping", dependencies=[InternalOnly])
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    return AsyncClient(transport=ASGITransport(app=mini), base_url="http://t")


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"X-Internal-Token": "wrong"}, 401),
        ({"X-Internal-Token": ""}, 401),
        ({"X-Internal-Token": get_settings().internal_token}, 200),
    ],
)
async def test_internal_token(
    internal_client: AsyncClient, headers: dict[str, str], status: int
) -> None:
    r = await internal_client.get("/internal/ping", headers=headers)
    assert r.status_code == status
    if status == 401:
        assert r.json() == {"detail": "Invalid internal token"}


async def test_admin_jwt_does_not_open_internal_endpoints(
    internal_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    assert (await internal_client.get("/internal/ping", headers=auth_headers)).status_code == 401
