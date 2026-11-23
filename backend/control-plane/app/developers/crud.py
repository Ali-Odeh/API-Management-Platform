"""All database work for developers (P.1).

A developer row belongs to exactly one account of the developers' GoTrue,
found by its id (`sub`), never by email, as for users: a token's email only
proves what GoTrue holds now. Their events are platform-level (no
workspace): `developer.create` by the developer themself on their first
portal request, `developer.block` and `developer.unblock` by the Platform
Admin.
"""

import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps import crud as apps_crud
from app.audit import crud as audit
from app.audit.changes import Changes, created, snapshot, updated
from app.audit.context import AuditContext
from app.core.errors import ForbiddenError, NotFoundError
from app.core.identity import TokenClaims
from app.core.pagination import PageParams, paginate
from app.developers.models import Developer, DeveloperStatus
from app.developers.schemas import DeveloperUpdate

AUDIT_FIELDS = ("email", "auth_user_id", "status")
ACCOUNT_CONFLICT = "account_conflict"
DEVELOPER_NOT_FOUND = "Developer not found"


def _normalize_email(email: str) -> str:
    return email.strip().lower()


async def _by_auth_user_id(db: AsyncSession, auth_user_id: uuid.UUID) -> Developer | None:
    return (
        await db.scalars(select(Developer).where(Developer.auth_user_id == auth_user_id))
    ).one_or_none()


async def get_or_create_from_token(
    db: AsyncSession, claims: TokenClaims, *, audit: AuditContext
) -> Developer:
    """The developer behind a verified portal token; their row is made just in time.

    A new account whose email a row already holds (the portal GoTrue's
    database was reset, say) gets 403 account_conflict instead of someone
    else's apps.
    """
    developer = await _by_auth_user_id(db, claims.auth_user_id)
    if developer is not None:
        return developer
    email = _normalize_email(claims.email)
    same_email = (await db.scalars(select(Developer).where(Developer.email == email))).one_or_none()
    if same_email is not None:
        if same_email.auth_user_id == claims.auth_user_id:  # a parallel first request made it
            return same_email
        raise ForbiddenError(ACCOUNT_CONFLICT)

    developer = Developer(
        id=uuid.uuid4(),
        auth_user_id=claims.auth_user_id,
        email=email,
        status=DeveloperStatus.ACTIVE,
    )
    db.add(developer)
    by_themself = dataclasses.replace(audit, actor_user_id=developer.id, actor_email=email)
    _record(db, by_themself, developer, "developer.create", created(_snapshot(developer)))
    try:
        await db.commit()
    except IntegrityError:
        # Two first requests at once: the other one may have made the row.
        await db.rollback()
        existing = await _by_auth_user_id(db, claims.auth_user_id)
        if existing is None:  # another account took the email meanwhile
            raise ForbiddenError(ACCOUNT_CONFLICT) from None
        return existing
    await db.refresh(developer)
    return developer


async def list_developers(
    db: AsyncSession, *, page: PageParams, status: DeveloperStatus | None = None
) -> tuple[list[Developer], int]:
    """One page of developers, newest first."""
    query = select(Developer).order_by(Developer.created_at.desc(), Developer.id)
    if status is not None:
        query = query.where(Developer.status == status)
    return await paginate(db, query, page)


async def get_developer(
    db: AsyncSession, developer_id: uuid.UUID, *, lock: bool = False
) -> Developer:
    developer = await db.get(Developer, developer_id, with_for_update=lock, populate_existing=lock)
    if developer is None:
        raise NotFoundError(DEVELOPER_NOT_FOUND)
    return developer


async def update_developer(
    db: AsyncSession, *, developer_id: uuid.UUID, data: DeveloperUpdate, audit: AuditContext
) -> Developer:
    """Block or unblock a developer (Platform Admin). Blocked, every portal
    request answers 403 developer_blocked from the next one on, and the keys
    of their apps stop working at the gateway, in every workspace (P.2)."""
    developer = await get_developer(db, developer_id, lock=True)
    before = _snapshot(developer)
    if data.status is not None:
        developer.status = data.status
    if changes := updated(before, _snapshot(developer)):
        await apps_crud.follow_developer(db, developer.id, developer.status)
        action = (
            "developer.block"
            if developer.status == DeveloperStatus.BLOCKED
            else "developer.unblock"
        )
        _record(db, audit, developer, action, changes)
    await db.commit()
    await db.refresh(developer)
    return developer


def _snapshot(developer: Developer) -> dict[str, object]:
    return snapshot(developer, AUDIT_FIELDS)


def _record(
    db: AsyncSession, audit_ctx: AuditContext, developer: Developer, action: str, changes: Changes
) -> None:
    audit.record(
        db,
        audit_ctx,
        workspace_id=None,
        action=action,
        target_type="developer",
        target_id=developer.id,
        target_name=developer.email,
        changes=changes,
    )
