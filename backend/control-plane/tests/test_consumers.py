"""Consumers: database rules and the /api/v1/consumers API."""

import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import TextClause, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.consumers.models import Consumer, ConsumerStatus
from app.keys.models import ApiKey
from app.plans.crud import get_default_plan
from app.workspaces.models import Workspace
from tests.factories import expect_violation, make_api_key, make_consumer, make_plan, make_workspace

URL = "/api/v1/workspaces/default/consumers"
Headers = dict[str, str]


# =====================================================================
# Database rules
# =====================================================================


async def test_defaults(db: AsyncSession, workspace: Workspace) -> None:
    consumer = await make_consumer(db, workspace.id)
    await db.refresh(consumer)
    assert consumer.status is ConsumerStatus.ACTIVE  # a real enum, not just a string
    assert consumer.plan_id == (await get_default_plan(db, workspace.id)).id


async def test_name_unique_per_workspace(
    db: AsyncSession, workspace: Workspace, other_workspace: Workspace
) -> None:
    await make_consumer(db, workspace.id, name="mobile-app")
    await make_consumer(db, other_workspace.id, name="mobile-app")  # other tenant: fine
    await expect_violation(
        db, "uq_consumers_workspace_id_name", make_consumer(db, workspace.id, name="mobile-app")
    )


async def test_email_may_repeat(db: AsyncSession, workspace: Workspace) -> None:
    # One developer can own several apps.
    await make_consumer(db, workspace.id, name="app-1", email="dev@example.com")
    await make_consumer(db, workspace.id, name="app-2", email="dev@example.com")


async def test_unknown_status_rejected(db: AsyncSession, workspace: Workspace) -> None:
    consumer = await make_consumer(db, workspace.id)
    # Raw SQL bypasses Python's enum, so this really tests the database CHECK.
    statement = text("UPDATE consumers SET status = 'deleted' WHERE id = :id")
    await expect_violation(
        db, "ck_consumers_status", _execute_and_commit(db, statement, {"id": consumer.id})
    )


async def test_the_plan_must_be_in_the_consumers_workspace(
    db: AsyncSession, workspace: Workspace
) -> None:
    other = await make_workspace(db, slug="globex")
    theirs = await make_plan(db, other.id)
    await expect_violation(
        db, "fk_consumers_plan", make_consumer(db, workspace.id, plan_id=theirs.id)
    )


async def test_deleting_consumer_deletes_its_keys(db: AsyncSession, workspace: Workspace) -> None:
    consumer = await make_consumer(db, workspace.id)
    await make_api_key(db, workspace.id, consumer.id)
    await make_api_key(db, workspace.id, consumer.id)

    await db.execute(text("DELETE FROM consumers WHERE id = :id"), {"id": consumer.id})
    await db.commit()

    # Count with real queries: db.get() would answer from the session's
    # in-memory cache, which doesn't know about the raw DELETE above.
    assert await _count(db, Consumer) == 0
    assert await _count(db, ApiKey) == 0


# =====================================================================
# API
# =====================================================================


async def create(client: AsyncClient, headers: Headers, **overrides: Any) -> Response:
    body = {"name": "mobile-app", "email": "dev@example.com"} | overrides
    return await client.post(URL, json=body, headers=headers)


# ---- Create


async def test_create_returns_201_with_defaults(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    r = await create(client, auth_headers, email="Dev@Example.COM")
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "mobile-app"
    assert body["email"] == "dev@example.com"  # stored lowercase
    assert body["status"] == "active"
    assert body["plan_id"] == str((await get_default_plan(db, workspace.id)).id)


async def test_create_can_set_status_and_plan(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    pro = await make_plan(db, workspace.id)
    r = await create(client, auth_headers, status="blocked", plan_id=str(pro.id))
    assert r.json()["status"] == "blocked"
    assert r.json()["plan_id"] == str(pro.id)


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "Mobile App"},  # capitals and space
        {"email": "not-an-email"},
        {"email": "dev@"},
        {"status": "deleted"},
        {"plan_id": "not-a-uuid"},
        {"plan_id": str(uuid.uuid4())},  # no such plan here
    ],
)
async def test_create_invalid_input_is_422(
    client: AsyncClient, auth_headers: Headers, overrides: dict[str, Any]
) -> None:
    assert (await create(client, auth_headers, **overrides)).status_code == 422


async def test_duplicate_name_is_409(client: AsyncClient, auth_headers: Headers) -> None:
    await create(client, auth_headers)
    r = await create(client, auth_headers, email="other@example.com")
    assert r.status_code == 409
    assert r.json() == {"detail": "A consumer named 'mobile-app' already exists"}


async def test_parallel_creates_with_same_name(client: AsyncClient, auth_headers: Headers) -> None:
    results = await asyncio.gather(*(create(client, auth_headers) for _ in range(2)))
    assert sorted(r.status_code for r in results) == [201, 409]


# ---- List / get


async def test_list_newest_first_and_filter_by_status(
    client: AsyncClient, auth_headers: Headers
) -> None:
    await create(client, auth_headers, name="app-a")
    await create(client, auth_headers, name="app-b", status="blocked")
    await create(client, auth_headers, name="app-c")

    everything = (await client.get(URL, headers=auth_headers)).json()
    assert everything["total"] == 3
    assert [c["name"] for c in everything["items"]] == ["app-c", "app-b", "app-a"]

    blocked = (await client.get(URL, params={"status": "blocked"}, headers=auth_headers)).json()
    assert [c["name"] for c in blocked["items"]] == ["app-b"]
    assert blocked["total"] == 1

    active = (await client.get(URL, params={"status": "active"}, headers=auth_headers)).json()
    assert active["total"] == 2


async def test_unknown_status_filter_is_422(client: AsyncClient, auth_headers: Headers) -> None:
    r = await client.get(URL, params={"status": "deleted"}, headers=auth_headers)
    assert r.status_code == 422


async def test_get_one_and_unknown(client: AsyncClient, auth_headers: Headers) -> None:
    created = (await create(client, auth_headers)).json()
    assert (await client.get(f"{URL}/{created['id']}", headers=auth_headers)).json() == created
    r = await client.get(f"{URL}/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404
    assert r.json() == {"detail": "Consumer not found"}


# ---- Update (including block)


async def test_block_and_unblock(client: AsyncClient, auth_headers: Headers) -> None:
    created = (await create(client, auth_headers)).json()
    path = f"{URL}/{created['id']}"

    blocked = await client.patch(path, json={"status": "blocked"}, headers=auth_headers)
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["plan_id"] == created["plan_id"]  # untouched

    unblocked = await client.patch(path, json={"status": "active"}, headers=auth_headers)
    assert unblocked.json()["status"] == "active"


async def test_patch_plan_and_email(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    pro = await make_plan(db, workspace.id)
    created = (await create(client, auth_headers)).json()
    r = await client.patch(
        f"{URL}/{created['id']}",
        json={"plan_id": str(pro.id), "email": "NEW@example.com"},
        headers=auth_headers,
    )
    assert r.json()["plan_id"] == str(pro.id)
    assert r.json()["email"] == "new@example.com"
    assert r.json()["name"] == "mobile-app"


async def test_patch_to_taken_name_is_409(client: AsyncClient, auth_headers: Headers) -> None:
    await create(client, auth_headers, name="app-a")
    other = (await create(client, auth_headers, name="app-b")).json()
    r = await client.patch(f"{URL}/{other['id']}", json={"name": "app-a"}, headers=auth_headers)
    assert r.status_code == 409


@pytest.mark.parametrize("field", ["name", "email", "status", "plan_id"])
async def test_patch_explicit_null_is_422(
    client: AsyncClient, auth_headers: Headers, field: str
) -> None:
    created = (await create(client, auth_headers)).json()
    r = await client.patch(f"{URL}/{created['id']}", json={field: None}, headers=auth_headers)
    assert r.status_code == 422


async def test_patch_unknown_is_404(client: AsyncClient, auth_headers: Headers) -> None:
    r = await client.patch(
        f"{URL}/{uuid.uuid4()}", json={"status": "blocked"}, headers=auth_headers
    )
    assert r.status_code == 404


# ---- Delete


async def test_delete_removes_consumer_and_its_keys(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    created = (await create(client, auth_headers)).json()
    consumer_id = uuid.UUID(created["id"])
    await make_api_key(db, workspace.id, consumer_id)
    await make_api_key(db, workspace.id, consumer_id)

    r = await client.delete(f"{URL}/{consumer_id}", headers=auth_headers)
    assert r.status_code == 204
    assert (await client.get(f"{URL}/{consumer_id}", headers=auth_headers)).status_code == 404
    assert await _count(db, ApiKey) == 0


async def test_delete_unknown_is_404(client: AsyncClient, auth_headers: Headers) -> None:
    r = await client.delete(f"{URL}/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


# ---- Auth and isolation


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", URL), ("POST", URL)]
    + [(method, f"{URL}/{uuid.uuid4()}") for method in ("GET", "PATCH", "DELETE")],
)
async def test_every_endpoint_needs_a_token(client: AsyncClient, method: str, path: str) -> None:
    assert (await client.request(method, path, json={})).status_code == 401


async def test_other_workspace_consumers_are_invisible(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, other_workspace: Workspace
) -> None:
    theirs = await make_consumer(db, other_workspace.id)
    path = f"{URL}/{theirs.id}"
    block = {"status": "blocked"}

    assert (await client.get(URL, headers=auth_headers)).json() == {"items": [], "total": 0}
    assert (await client.get(path, headers=auth_headers)).status_code == 404
    assert (await client.patch(path, json=block, headers=auth_headers)).status_code == 404
    assert (await client.delete(path, headers=auth_headers)).status_code == 404
    # ...and it really is untouched.
    await db.refresh(theirs)
    assert theirs.status is ConsumerStatus.ACTIVE


async def test_same_name_allowed_in_another_workspace(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, other_workspace: Workspace
) -> None:
    await make_consumer(db, other_workspace.id, name="mobile-app")
    assert (await create(client, auth_headers, name="mobile-app")).status_code == 201


# ---- Helpers


async def _count(db: AsyncSession, model: type[Consumer] | type[ApiKey]) -> int:
    return await db.scalar(select(func.count()).select_from(model)) or 0


async def _execute_and_commit(
    db: AsyncSession, statement: TextClause, params: dict[str, object]
) -> None:
    await db.execute(statement, params)
    await db.commit()
