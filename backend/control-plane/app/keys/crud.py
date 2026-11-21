"""All database work for API keys, always scoped to one workspace (option C).

The raw key exists only inside create_key(): it is generated, hashed, returned
to the caller once, and never stored.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import crud as audit
from app.audit.changes import Changes, created, snapshot, updated
from app.audit.context import AuditContext
from app.consumers import crud as consumers_crud
from app.core.db import commit_or_raise
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.pagination import PageParams, paginate
from app.core.security import generate_api_key, hash_api_key
from app.keys.models import KEY_PREFIX_LENGTH, UQ_API_KEY_HASH, ApiKey
from app.keys.schemas import KeyCreate

MAX_ACTIVE_KEYS_PER_CONSUMER = 10
# What a key's audit events record: never its hash, and the raw key exists
# only in create_key's answer; the prefix is how people tell keys apart.
AUDIT_FIELDS = ("consumer_id", "name", "key_prefix")
_GENERATE_ATTEMPTS = 3


class _HashCollisionError(AppError):
    """Internal: a freshly generated key hashed to an existing hash. Never leaves this module."""


async def list_keys(
    db: AsyncSession, workspace_id: uuid.UUID, *, consumer_id: uuid.UUID, page: PageParams
) -> tuple[list[ApiKey], int]:
    """One page of a consumer's keys (newest first), revoked ones included."""
    await consumers_crud.get_consumer(db, workspace_id=workspace_id, consumer_id=consumer_id)
    query = (
        select(ApiKey)
        .where(ApiKey.workspace_id == workspace_id, ApiKey.consumer_id == consumer_id)
        .order_by(ApiKey.created_at.desc(), ApiKey.id)
    )
    return await paginate(db, query, page)


async def create_key(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    consumer_id: uuid.UUID,
    data: KeyCreate,
    audit: AuditContext,
) -> tuple[ApiKey, str]:
    """Generate a key for a consumer. Returns the stored row and the raw key."""
    await _lock_consumer(db, workspace_id=workspace_id, consumer_id=consumer_id)
    if await _count_active_keys(db, consumer_id=consumer_id) >= MAX_ACTIVE_KEYS_PER_CONSUMER:
        await db.rollback()  # release the lock
        raise ConflictError(
            f"A consumer can have at most {MAX_ACTIVE_KEYS_PER_CONSUMER} active keys. "
            "Revoke one first."
        )

    for _ in range(_GENERATE_ATTEMPTS):
        raw_key = generate_api_key()
        key = ApiKey(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            consumer_id=consumer_id,
            name=data.name,
            key_prefix=raw_key[:KEY_PREFIX_LENGTH],
            key_hash=hash_api_key(raw_key),
        )
        db.add(key)
        # Inside the loop: a collision's rollback takes the staged event with it.
        _record(db, audit, key, "key.create", created(snapshot(key, AUDIT_FIELDS)))
        try:
            await commit_or_raise(db, {UQ_API_KEY_HASH: _HashCollisionError("hash collision")})
        except _HashCollisionError:
            # 2^128 possible keys make this practically impossible, but if it
            # happens the right answer is simply a new key, not an error.
            # The rollback released the lock, so take it again before retrying.
            await _lock_consumer(db, workspace_id=workspace_id, consumer_id=consumer_id)
            continue
        await db.refresh(key)
        return key, raw_key
    raise RuntimeError("Could not generate a unique API key")


async def _lock_consumer(
    db: AsyncSession, workspace_id: uuid.UUID, *, consumer_id: uuid.UUID
) -> None:
    """404 if the consumer isn't in the workspace; otherwise lock its row until commit.

    The lock serializes key creation per consumer: two parallel requests can't
    both see "9 active keys" and both add one; the second waits, then counts 10.
    We only use plain ids afterwards: a rollback expires loaded objects, and
    reading an expired attribute in async SQLAlchemy fails (MissingGreenlet).
    """
    await consumers_crud.get_consumer(
        db, workspace_id=workspace_id, consumer_id=consumer_id, for_update=True
    )


async def revoke_key(
    db: AsyncSession, workspace_id: uuid.UUID, *, key_id: uuid.UUID, audit: AuditContext
) -> None:
    """Mark a key revoked. Idempotent: revoking again keeps the first revoked_at
    (and, since nothing changes, writes no second audit event)."""
    key = await db.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.workspace_id == workspace_id)
    )
    if key is None:
        raise NotFoundError("API key not found")
    if key.revoked_at is None:
        before = snapshot(key, ("revoked_at",))
        key.revoked_at = datetime.now(UTC)
        _record(db, audit, key, "key.revoke", updated(before, snapshot(key, ("revoked_at",))))
        await db.commit()


async def _count_active_keys(db: AsyncSession, *, consumer_id: uuid.UUID) -> int:
    query = select(func.count()).where(
        ApiKey.consumer_id == consumer_id, ApiKey.revoked_at.is_(None)
    )
    return await db.scalar(query) or 0


def _record(
    db: AsyncSession, audit_ctx: AuditContext, key: ApiKey, action: str, changes: Changes
) -> None:
    audit.record(
        db,
        audit_ctx,
        workspace_id=key.workspace_id,
        action=action,
        target_type="key",
        target_id=key.id,
        target_name=key.key_prefix,
        changes=changes,
    )
