"""Plans (V2.2): the /plans API, its rules, and consumers on plans."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEvent
from app.plans.models import Plan
from app.workspaces.models import Workspace
from tests.factories import expect_violation, make_consumer, make_plan, make_workspace
from tests.test_gateway_config import Watch

URL = "/api/v1/workspaces/default/plans"
Headers = dict[str, str]


async def create(client: AsyncClient, headers: Headers, **overrides: Any) -> Response:
    body = {"name": "Pro", "rate_limit_per_min": 600} | overrides
    return await client.post(URL, json=body, headers=headers)


async def plans_of(db: AsyncSession, workspace_id: uuid.UUID) -> dict[str, Plan]:
    # Fresh values for the plans only (expire_all would also expire the fixtures).
    query = select(Plan).where(Plan.workspace_id == workspace_id)
    rows = await db.scalars(query.execution_options(populate_existing=True))
    return {p.name: p for p in rows}


# ---- Every workspace has its default plan


async def test_a_new_workspace_starts_with_its_default_plan(
    client: AsyncClient, mfa_headers: Headers, db: AsyncSession, admin: Any
) -> None:
    r = await client.post(
        "/api/v1/workspaces",
        json={"name": "Acme", "slug": "acme", "owner_email": admin.email},
        headers=mfa_headers,
    )
    assert r.status_code == 201, r.text

    [default] = (await plans_of(db, uuid.UUID(r.json()["id"]))).values()
    assert (default.name, default.rate_limit_per_min, default.monthly_quota) == (
        "Default",
        60,
        None,
    )
    assert default.is_default


async def test_one_default_per_workspace_in_the_database(
    db: AsyncSession, workspace: Workspace
) -> None:
    await expect_violation(
        db,
        "uq_plans_one_default_per_workspace",
        make_plan(db, workspace.id, name="Second", is_default=True),
    )


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"rate_limit_per_min": 0}, "ck_plans_rate_limit_range"),
        ({"rate_limit_per_min": 100_001}, "ck_plans_rate_limit_range"),
        ({"monthly_quota": 0}, "ck_plans_monthly_quota_range"),
    ],
)
async def test_the_database_checks_the_limits(
    db: AsyncSession, workspace: Workspace, fields: dict[str, int], constraint: str
) -> None:
    await expect_violation(db, constraint, make_plan(db, workspace.id, **fields))


# ---- The API


async def test_create_list_and_read(client: AsyncClient, auth_headers: Headers) -> None:
    r = await create(
        client, auth_headers, name="  Free  ", rate_limit_per_min=60, monthly_quota=1000
    )
    assert r.status_code == 201, r.text
    free = r.json()
    assert (free["name"], free["monthly_quota"], free["is_default"]) == ("Free", 1000, False)
    await create(client, auth_headers)

    listed = (await client.get(URL, headers=auth_headers)).json()
    # The default first, then by rate limit.
    assert [p["name"] for p in listed["items"]] == ["Default", "Free", "Pro"]
    assert (await client.get(f"{URL}/{free['id']}", headers=auth_headers)).json() == free


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "bad\nname"},
        {"rate_limit_per_min": 0},
        {"rate_limit_per_min": 100_001},
        {"monthly_quota": 0},
    ],
)
async def test_invalid_input_is_422(
    client: AsyncClient, auth_headers: Headers, overrides: dict[str, Any]
) -> None:
    assert (await create(client, auth_headers, **overrides)).status_code == 422


async def test_a_taken_name_is_409(client: AsyncClient, auth_headers: Headers) -> None:
    await create(client, auth_headers)
    r = await create(client, auth_headers)
    assert (r.status_code, r.json()) == (409, {"detail": "A plan named 'Pro' already exists"})


async def test_a_plan_above_the_workspaces_limit_is_422(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    await db.execute(
        update(Workspace).where(Workspace.id == workspace.id).values(rate_limit_per_min=500)
    )
    await db.commit()

    above = await create(client, auth_headers, rate_limit_per_min=501)
    at = await create(client, auth_headers, rate_limit_per_min=500)
    raised = await client.patch(
        f"{URL}/{at.json()['id']}", json={"rate_limit_per_min": 900}, headers=auth_headers
    )

    assert above.status_code == 422
    assert above.json() == {
        "detail": "rate_limit_per_min is above the workspace's limit of 500 requests per minute"
    }
    assert at.status_code == 201
    assert raised.status_code == 422


async def test_the_default_moves_and_is_never_unset(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    old = (await plans_of(db, workspace.id))["Default"].id
    pro = (await create(client, auth_headers, is_default=True)).json()

    plans = await plans_of(db, workspace.id)
    assert (plans["Pro"].is_default, plans["Default"].is_default) == (True, False)

    r = await client.patch(f"{URL}/{pro['id']}", json={"is_default": False}, headers=auth_headers)
    assert r.status_code == 422
    back = await client.patch(f"{URL}/{old}", json={"is_default": True}, headers=auth_headers)
    assert back.json()["is_default"] is True
    assert (await client.get(f"{URL}/{pro['id']}", headers=auth_headers)).json()[
        "is_default"
    ] is False


async def test_a_quota_is_removed_with_null(client: AsyncClient, auth_headers: Headers) -> None:
    pro = (await create(client, auth_headers, monthly_quota=10)).json()
    r = await client.patch(f"{URL}/{pro['id']}", json={"monthly_quota": None}, headers=auth_headers)
    assert r.json()["monthly_quota"] is None
    named = await client.patch(f"{URL}/{pro['id']}", json={"name": None}, headers=auth_headers)
    assert named.status_code == 422  # only the quota may be null


async def test_delete_rules(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    default = (await plans_of(db, workspace.id))["Default"].id
    used = (await make_plan(db, workspace.id, name="Used")).id
    await make_consumer(db, workspace.id, plan_id=used)
    spare = (await make_plan(db, workspace.id, name="Spare")).id

    assert (await client.delete(f"{URL}/{default}", headers=auth_headers)).json() == {
        "detail": "default_plan"
    }
    assert (await client.delete(f"{URL}/{used}", headers=auth_headers)).json() == {
        "detail": "plan_in_use"
    }
    assert (await client.delete(f"{URL}/{spare}", headers=auth_headers)).status_code == 204
    assert set(await plans_of(db, workspace.id)) == {"Default", "Used"}


async def test_a_plan_with_consumers_is_kept_by_the_database_too(
    db: AsyncSession, workspace: Workspace
) -> None:
    # If a consumer moves onto a plan while it is being deleted, the foreign
    # key refuses the delete (crud turns it into 409 plan_in_use).
    used = await make_plan(db, workspace.id, name="Used")
    await make_consumer(db, workspace.id, plan_id=used.id)
    await db.delete(used)
    await expect_violation(db, "fk_consumers_plan", db.commit())


async def test_another_workspaces_plan_is_not_found(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession
) -> None:
    globex = await make_workspace(db, slug="globex")
    theirs = await make_plan(db, globex.id)
    assert (await client.get(f"{URL}/{theirs.id}", headers=auth_headers)).status_code == 404
    r = await client.post(
        "/api/v1/workspaces/default/consumers",
        json={"name": "app", "email": "a@example.com", "plan_id": str(theirs.id)},
        headers=auth_headers,
    )
    assert (r.status_code, r.json()) == (422, {"detail": "Plan not found"})


async def test_plan_changes_are_audited(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession
) -> None:
    pro = (await create(client, auth_headers, monthly_quota=5)).json()
    await client.patch(f"{URL}/{pro['id']}", json={"monthly_quota": 50}, headers=auth_headers)
    await client.delete(f"{URL}/{pro['id']}", headers=auth_headers)

    events = (
        await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.target_type == "plan")
            .order_by(AuditEvent.created_at)
        )
    ).all()
    assert [(e.action, e.changes.get("monthly_quota")) for e in events] == [
        ("plan.create", [None, 5]),
        ("plan.update", [5, 50]),
        ("plan.delete", [50, None]),
    ]


# ---- Plans in the gateway's config


async def test_a_plan_change_and_a_move_reach_the_gateway(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    pro = await make_plan(db, workspace.id)
    consumer = await make_consumer(db, workspace.id)
    watch = await Watch().start()

    await client.patch(f"{URL}/{pro.id}", json={"rate_limit_per_min": 700}, headers=auth_headers)
    await watch.assert_bumped(workspace.id)
    await client.patch(
        f"/api/v1/workspaces/default/consumers/{consumer.id}",
        json={"plan_id": str(pro.id)},
        headers=auth_headers,
    )
    await watch.assert_bumped(workspace.id)
    await client.patch(f"{URL}/{pro.id}", json={"name": "Renamed"}, headers=auth_headers)
    await watch.assert_unchanged()  # the gateway doesn't use the name
