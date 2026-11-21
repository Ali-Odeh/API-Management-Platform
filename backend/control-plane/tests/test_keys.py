"""API keys: database rules and the keys API (generate, list, revoke)."""

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.context import AuditContext
from app.consumers.models import Consumer
from app.core.security import hash_api_key
from app.keys import crud as keys_crud
from app.keys.crud import MAX_ACTIVE_KEYS_PER_CONSUMER
from app.keys.schemas import KeyCreate
from app.workspaces.models import Workspace
from tests.factories import expect_violation, make_api_key, make_consumer

Headers = dict[str, str]


@pytest.fixture
async def consumer(db: AsyncSession, workspace: Workspace) -> Consumer:
    return await make_consumer(db, workspace.id)


# =====================================================================
# Database rules
# =====================================================================


async def test_new_key_is_active_and_unused(db: AsyncSession, consumer: Consumer) -> None:
    key = await make_api_key(
        db, consumer.workspace_id, consumer.id, raw_key="ak_live_" + "3f9a" * 8
    )
    assert key.key_prefix == "ak_live_3f9a"
    assert key.is_active is True
    assert key.last_used_at is None


async def test_key_hash_unique_across_all_workspaces(
    db: AsyncSession, consumer: Consumer, other_workspace: Workspace
) -> None:
    # The gateway looks a key up by its hash alone, so it must be globally unique.
    theirs = await make_consumer(db, other_workspace.id)
    first = await make_api_key(db, consumer.workspace_id, consumer.id)
    await expect_violation(
        db,
        "uq_api_keys_key_hash",
        make_api_key(db, other_workspace.id, theirs.id, key_hash=first.key_hash),
    )


async def test_key_hash_must_be_sha256_hex_length(db: AsyncSession, consumer: Consumer) -> None:
    await expect_violation(
        db,
        "ck_api_keys_key_hash_sha256",
        make_api_key(db, consumer.workspace_id, consumer.id, key_hash="too-short"),
    )


async def test_key_prefix_must_be_12_chars(db: AsyncSession, consumer: Consumer) -> None:
    await expect_violation(
        db,
        "ck_api_keys_key_prefix_len",
        make_api_key(db, consumer.workspace_id, consumer.id, key_prefix="ak_"),
    )


async def test_key_cannot_belong_to_another_workspaces_consumer(
    db: AsyncSession, workspace: Workspace, other_workspace: Workspace
) -> None:
    # Option C safety: the composite foreign key makes a cross-tenant link impossible,
    # even if application code had a bug.
    theirs = await make_consumer(db, other_workspace.id)
    await expect_violation(db, "fk_api_keys_consumer", make_api_key(db, workspace.id, theirs.id))


# =====================================================================
# API
# =====================================================================


def keys_url(consumer_id: uuid.UUID | str) -> str:
    return f"/api/v1/workspaces/default/consumers/{consumer_id}/keys"


def key_url(key_id: uuid.UUID | str) -> str:
    return f"/api/v1/workspaces/default/keys/{key_id}"


async def create(
    client: AsyncClient, headers: Headers, consumer_id: uuid.UUID, name: str = "mobile app"
) -> Response:
    return await client.post(keys_url(consumer_id), json={"name": name}, headers=headers)


# ---- Generate


async def test_generate_returns_raw_key_once(
    client: AsyncClient, auth_headers: Headers, consumer: Consumer
) -> None:
    r = await create(client, auth_headers, consumer.id, name="  mobile app, iOS ")
    assert r.status_code == 201
    assert r.headers["Cache-Control"] == "no-store"  # never cached by a browser or proxy
    body = r.json()
    raw = body["key"]
    assert raw.startswith("ak_live_") and len(raw) == 40
    assert body["key_prefix"] == raw[:12]
    assert body["name"] == "mobile app, iOS"  # trimmed
    assert body["consumer_id"] == str(consumer.id)
    assert body["is_active"] is True
    assert body["revoked_at"] is None
    assert body["last_used_at"] is None


async def test_raw_key_is_never_stored_or_listed(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, consumer: Consumer
) -> None:
    raw = (await create(client, auth_headers, consumer.id)).json()["key"]

    # Not in any column of the table...
    rows = (await db.execute(text("SELECT row_to_json(api_keys)::text FROM api_keys"))).scalars()
    stored = " ".join(rows)
    assert raw not in stored
    # ...only its hash is.
    assert hash_api_key(raw) in stored

    # Not in the list endpoint either.
    listing = await client.get(keys_url(consumer.id), headers=auth_headers)
    assert raw not in listing.text
    assert "key" not in listing.json()["items"][0]


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
async def test_bad_name_is_422(
    client: AsyncClient, auth_headers: Headers, consumer: Consumer, name: str
) -> None:
    assert (await create(client, auth_headers, consumer.id, name=name)).status_code == 422


async def test_unknown_consumer_is_404(client: AsyncClient, auth_headers: Headers) -> None:
    r = await create(client, auth_headers, uuid.uuid4())
    assert r.status_code == 404
    assert r.json() == {"detail": "Consumer not found"}


async def test_blocked_consumer_can_still_get_keys(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, workspace: Workspace
) -> None:
    blocked = await make_consumer(db, workspace.id, status="blocked")
    assert (await create(client, auth_headers, blocked.id)).status_code == 201


# ---- Active key limit


async def test_active_key_limit_and_revoking_frees_a_slot(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, consumer: Consumer
) -> None:
    keys = [
        await make_api_key(db, consumer.workspace_id, consumer.id)
        for _ in range(MAX_ACTIVE_KEYS_PER_CONSUMER)
    ]
    r = await create(client, auth_headers, consumer.id)
    assert r.status_code == 409
    assert "at most 10 active keys" in r.json()["detail"]

    await client.delete(key_url(keys[0].id), headers=auth_headers)
    assert (await create(client, auth_headers, consumer.id)).status_code == 201


async def test_active_key_limit_holds_under_parallel_requests(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, consumer: Consumer
) -> None:
    for _ in range(MAX_ACTIVE_KEYS_PER_CONSUMER - 1):
        await make_api_key(db, consumer.workspace_id, consumer.id)
    # One free slot, two requests at once: the row lock lets only one through.
    results = await asyncio.gather(*(create(client, auth_headers, consumer.id) for _ in range(2)))
    assert sorted(r.status_code for r in results) == [201, 409]


# ---- Hash collision (practically impossible, but handled)


def _keys(*raw_keys: str) -> Iterator[str]:
    yield from raw_keys


async def test_hash_collision_retries_with_a_new_key(
    client: AsyncClient,
    auth_headers: Headers,
    db: AsyncSession,
    consumer: Consumer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken = "ak_live_" + "a" * 32
    fresh = "ak_live_" + "b" * 32
    await make_api_key(db, consumer.workspace_id, consumer.id, raw_key=taken)
    generated = _keys(taken, fresh)
    monkeypatch.setattr(keys_crud, "generate_api_key", lambda: next(generated))

    r = await create(client, auth_headers, consumer.id)
    assert r.status_code == 201
    assert r.json()["key"] == fresh


async def test_gives_up_after_repeated_collisions(
    db: AsyncSession, consumer: Consumer, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = "ak_live_" + "a" * 32
    await make_api_key(db, consumer.workspace_id, consumer.id, raw_key=taken)
    monkeypatch.setattr(keys_crud, "generate_api_key", lambda: taken)

    with pytest.raises(RuntimeError, match="unique API key"):
        await keys_crud.create_key(
            db,
            workspace_id=consumer.workspace_id,
            consumer_id=consumer.id,
            data=KeyCreate(name="x"),
            audit=AuditContext.system(),
        )


# ---- List


async def test_list_newest_first_with_revoked_keys(
    client: AsyncClient, auth_headers: Headers, consumer: Consumer
) -> None:
    first = (await create(client, auth_headers, consumer.id, name="first")).json()
    await create(client, auth_headers, consumer.id, name="second")
    await client.delete(key_url(first["id"]), headers=auth_headers)

    listing = (await client.get(keys_url(consumer.id), headers=auth_headers)).json()
    assert listing["total"] == 2
    assert [k["name"] for k in listing["items"]] == ["second", "first"]
    assert [k["is_active"] for k in listing["items"]] == [True, False]
    assert listing["items"][1]["revoked_at"] is not None


async def test_list_only_this_consumers_keys(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, consumer: Consumer
) -> None:
    other = await make_consumer(db, consumer.workspace_id, name="other-app")
    await create(client, auth_headers, consumer.id)
    await create(client, auth_headers, other.id)
    listing = (await client.get(keys_url(consumer.id), headers=auth_headers)).json()
    assert listing["total"] == 1


async def test_list_unknown_consumer_is_404(client: AsyncClient, auth_headers: Headers) -> None:
    assert (await client.get(keys_url(uuid.uuid4()), headers=auth_headers)).status_code == 404


# ---- Revoke


async def test_revoke_is_idempotent_and_keeps_first_time(
    client: AsyncClient, auth_headers: Headers, consumer: Consumer
) -> None:
    created = (await create(client, auth_headers, consumer.id)).json()

    assert (await client.delete(key_url(created["id"]), headers=auth_headers)).status_code == 204
    first = (await client.get(keys_url(consumer.id), headers=auth_headers)).json()["items"][0]
    assert first["is_active"] is False

    assert (await client.delete(key_url(created["id"]), headers=auth_headers)).status_code == 204
    again = (await client.get(keys_url(consumer.id), headers=auth_headers)).json()["items"][0]
    assert again["revoked_at"] == first["revoked_at"]


async def test_revoke_unknown_is_404(client: AsyncClient, auth_headers: Headers) -> None:
    r = await client.delete(key_url(uuid.uuid4()), headers=auth_headers)
    assert r.status_code == 404
    assert r.json() == {"detail": "API key not found"}


# ---- Auth and isolation


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", keys_url(uuid.uuid4())),
        ("POST", keys_url(uuid.uuid4())),
        ("DELETE", key_url(uuid.uuid4())),
    ],
)
async def test_every_endpoint_needs_a_token(client: AsyncClient, method: str, path: str) -> None:
    assert (await client.request(method, path, json={"name": "x"})).status_code == 401


async def test_other_workspace_keys_are_out_of_reach(
    client: AsyncClient, auth_headers: Headers, db: AsyncSession, other_workspace: Workspace
) -> None:
    theirs = await make_consumer(db, other_workspace.id)
    their_key = await make_api_key(db, other_workspace.id, theirs.id)

    assert (await client.get(keys_url(theirs.id), headers=auth_headers)).status_code == 404
    assert (await create(client, auth_headers, theirs.id)).status_code == 404
    assert (await client.delete(key_url(their_key.id), headers=auth_headers)).status_code == 404
    await db.refresh(their_key)
    assert their_key.is_active is True  # untouched
