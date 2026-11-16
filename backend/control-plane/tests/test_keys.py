"""API keys: database rules. (Generation and revocation arrive in step 2.3.)"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.workspaces.models import Workspace
from tests.factories import expect_violation, make_api_key, make_consumer


async def test_new_key_is_active_and_unused(db: AsyncSession, workspace: Workspace) -> None:
    consumer = await make_consumer(db, workspace.id)
    key = await make_api_key(db, workspace.id, consumer.id, raw_key="ak_live_" + "3f9a" * 8)
    assert key.key_prefix == "ak_live_3f9a"
    assert key.revoked_at is None
    assert key.last_used_at is None


async def test_key_hash_unique_across_all_workspaces(
    db: AsyncSession, workspace: Workspace, other_workspace: Workspace
) -> None:
    # The gateway looks a key up by its hash alone, so it must be globally unique.
    ours = await make_consumer(db, workspace.id)
    theirs = await make_consumer(db, other_workspace.id)
    first = await make_api_key(db, workspace.id, ours.id)
    await expect_violation(
        db,
        "uq_api_keys_key_hash",
        make_api_key(db, other_workspace.id, theirs.id, key_hash=first.key_hash),
    )


async def test_key_hash_must_be_sha256_hex_length(db: AsyncSession, workspace: Workspace) -> None:
    consumer = await make_consumer(db, workspace.id)
    await expect_violation(
        db,
        "ck_api_keys_key_hash_sha256",
        make_api_key(db, workspace.id, consumer.id, key_hash="too-short"),
    )


async def test_key_prefix_must_be_12_chars(db: AsyncSession, workspace: Workspace) -> None:
    consumer = await make_consumer(db, workspace.id)
    await expect_violation(
        db,
        "ck_api_keys_key_prefix_len",
        make_api_key(db, workspace.id, consumer.id, key_prefix="ak_"),
    )


async def test_key_cannot_belong_to_another_workspaces_consumer(
    db: AsyncSession, workspace: Workspace, other_workspace: Workspace
) -> None:
    # Option C safety: the composite foreign key makes a cross-tenant link impossible,
    # even if application code had a bug.
    theirs = await make_consumer(db, other_workspace.id)
    await expect_violation(db, "fk_api_keys_consumer", make_api_key(db, workspace.id, theirs.id))
