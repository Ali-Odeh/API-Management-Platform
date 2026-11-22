"""Workspace quotas and limits (decision 6): 409 quota_exceeded, PATCH /limits."""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEvent
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.permissions import Role
from app.services.models import Service
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceStatus
from tests.factories import (
    make_consumer,
    make_member,
    make_route,
    make_service,
    make_user,
    make_workspace,
)
from tests.fakes import bearer, make_token
from tests.test_api_guards import set_status

ACME = "/api/v1/workspaces/acme"
Headers = dict[str, str]


@pytest.fixture
async def acme(db: AsyncSession) -> Workspace:
    return await make_workspace(db, slug="acme")


@pytest.fixture
async def owner(db: AsyncSession, acme: Workspace) -> Headers:
    person = await make_user(db, email="olivia@acme.test")
    await make_member(db, acme.id, person.id, Role.OWNER)
    assert person.auth_user_id is not None
    return bearer(make_token(person.auth_user_id, person.email))


async def _limit(acme: Workspace, **limits: int | None) -> None:
    async with SessionLocal() as session:
        workspace = await session.get_one(Workspace, acme.id)
        for column, value in limits.items():
            setattr(workspace, column, value)
        await session.commit()


async def _service(client: AsyncClient, owner: Headers, name: str) -> Any:
    body = {"name": name, "targets": [{"url": "http://mock-backend:9000"}]}
    return await client.post(f"{ACME}/services", json=body, headers=owner)


# ---- Each quota refuses the one too many


async def test_services_over_the_quota_are_409(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: Headers
) -> None:
    await _limit(acme, max_services=2)
    await make_service(db, acme.id, name="one")

    assert (await _service(client, owner, "two")).status_code == 201
    r = await _service(client, owner, "three")

    assert (r.status_code, r.json()) == (409, {"detail": "quota_exceeded"})


async def test_routes_over_the_quota_are_409(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: Headers
) -> None:
    await _limit(acme, max_routes=1)
    service = await make_service(db, acme.id)
    await make_route(db, acme.id, service.id, path_prefix="/v1/a")

    body = {"service_id": str(service.id), "path_prefix": "/v1/b", "methods": ["GET"]}
    r = await client.post(f"{ACME}/routes", json=body, headers=owner)

    assert (r.status_code, r.json()) == (409, {"detail": "quota_exceeded"})


async def test_consumers_over_the_quota_are_409(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: Headers
) -> None:
    await _limit(acme, max_consumers=1)
    await make_consumer(db, acme.id)

    body = {"name": "second", "email": "dev@example.com"}
    r = await client.post(f"{ACME}/consumers", json=body, headers=owner)

    assert (r.status_code, r.json()) == (409, {"detail": "quota_exceeded"})


async def test_the_platform_default_applies_without_an_own_limit(
    client: AsyncClient,
    db: AsyncSession,
    acme: Workspace,
    owner: Headers,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "workspace_max_services", 1)
    await make_service(db, acme.id)

    assert (await _service(client, owner, "two")).status_code == 409


async def test_quotas_count_per_workspace(
    client: AsyncClient, db: AsyncSession, acme: Workspace, owner: Headers
) -> None:
    other = await make_workspace(db, slug="globex")
    await make_service(db, other.id, name="theirs")
    await _limit(acme, max_services=1)

    assert (await _service(client, owner, "ours")).status_code == 201


async def test_a_refused_creation_leaves_no_event(
    client: AsyncClient, acme: Workspace, owner: Headers
) -> None:
    await _limit(acme, max_services=1)
    await _service(client, owner, "one")
    await _service(client, owner, "two")

    async with SessionLocal() as session:
        created = await session.scalar(
            select(func.count()).where(AuditEvent.action == "service.create")
        )
    assert created == 1


async def test_parallel_creations_never_overshoot(
    client: AsyncClient, acme: Workspace, owner: Headers
) -> None:
    # The workspace row lock makes them count one after the other: without it,
    # several would see "2 of 3" and all add one.
    await _limit(acme, max_services=3)

    answers = await asyncio.gather(*(_service(client, owner, f"s{i}") for i in range(10)))

    assert sorted(r.status_code for r in answers) == [201] * 3 + [409] * 7
    async with SessionLocal() as session:
        count = await session.scalar(select(func.count()).where(Service.workspace_id == acme.id))
    assert count == 3


async def test_lowering_a_quota_keeps_what_exists(
    client: AsyncClient,
    db: AsyncSession,
    acme: Workspace,
    owner: Headers,
    admin: User,
    mfa_headers: Headers,
) -> None:
    for name in ("one", "two"):
        await make_service(db, acme.id, name=name)

    r = await client.patch(f"{ACME}/limits", json={"max_services": 1}, headers=mfa_headers)

    assert r.status_code == 200
    services = await client.get(f"{ACME}/services", headers=owner)
    assert services.json()["total"] == 2
    assert (await _service(client, owner, "three")).status_code == 409


# ---- PATCH /limits (Platform Admin)


async def test_the_platform_admin_sets_and_resets_limits(
    client: AsyncClient, acme: Workspace, admin: User, mfa_headers: Headers
) -> None:
    r = await client.patch(
        f"{ACME}/limits", json={"max_routes": 20, "rate_limit_per_min": 100}, headers=mfa_headers
    )

    assert r.status_code == 200, r.text
    assert r.json()["limits"]["max_routes"] == 20
    assert r.json()["limit_overrides"]["max_routes"] == 20
    assert r.json()["limits"]["max_services"] == 100  # untouched: the default
    async with SessionLocal() as session:
        [event] = await session.scalars(
            select(AuditEvent).where(AuditEvent.action == "workspace.limits_change")
        )
    # In the workspace's own log, so its Owners see why a quota moved.
    assert (event.workspace_id, event.actor_type) == (acme.id, "platform_admin")
    assert event.changes == {"max_routes": [None, 20], "rate_limit_per_min": [None, 100]}

    # null puts a limit back to the platform default.
    r = await client.patch(f"{ACME}/limits", json={"max_routes": None}, headers=mfa_headers)

    assert r.json()["limits"]["max_routes"] == 500
    assert r.json()["limit_overrides"]["max_routes"] is None


async def test_unchanged_limits_record_nothing(
    client: AsyncClient, acme: Workspace, admin: User, mfa_headers: Headers
) -> None:
    for body in ({}, {"max_services": None}):
        r = await client.patch(f"{ACME}/limits", json=body, headers=mfa_headers)
        assert r.status_code == 200
    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count()).where(AuditEvent.action == "workspace.limits_change")
        )
    assert count == 0


@pytest.mark.parametrize(
    "body",
    [
        {"max_services": 0},
        {"max_routes": -1},
        {"rate_limit_per_min": 1_000_001},
        {"max_concurrent": "many"},
    ],
)
async def test_invalid_limits_are_422(
    client: AsyncClient, acme: Workspace, admin: User, mfa_headers: Headers, body: dict[str, Any]
) -> None:
    r = await client.patch(f"{ACME}/limits", json=body, headers=mfa_headers)
    assert r.status_code == 422


@pytest.mark.parametrize("status", [WorkspaceStatus.DELETED, WorkspaceStatus.PURGED])
async def test_a_deleted_workspaces_limits_are_frozen(
    client: AsyncClient,
    acme: Workspace,
    admin: User,
    mfa_headers: Headers,
    status: WorkspaceStatus,
) -> None:
    await set_status(acme.id, status)
    r = await client.patch(f"{ACME}/limits", json={"max_services": 5}, headers=mfa_headers)
    assert (r.status_code, r.json()) == (404, {"detail": "Workspace not found"})


async def test_unknown_workspace_limits_are_404(
    client: AsyncClient, admin: User, mfa_headers: Headers
) -> None:
    r = await client.patch(
        "/api/v1/workspaces/nope/limits", json={"max_services": 5}, headers=mfa_headers
    )
    assert r.status_code == 404
