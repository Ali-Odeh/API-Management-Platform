"""All database work for users, plus the GoTrue calls that go with it."""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.identity import TokenClaims
from app.gotrue.client import GoTrueAdmin
from app.users.models import User
from app.users.schemas import UserUpdate


def normalize_email(email: str) -> str:
    """'  Admin@Example.COM ' and 'admin@example.com' are the same person."""
    return email.strip().lower()


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    return user


async def _by_auth_user_id(db: AsyncSession, auth_user_id: uuid.UUID) -> User | None:
    result = await db.scalars(select(User).where(User.auth_user_id == auth_user_id))
    return result.one_or_none()


async def _by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.scalars(select(User).where(User.email == normalize_email(email)))
    return result.one_or_none()


async def get_or_create_from_token(db: AsyncSession, claims: TokenClaims) -> User:
    """The person behind a verified GoTrue token; their row is created just in time.

    Found by GoTrue's id first. Otherwise a row with the same email is linked
    to this account: the migrated admin or the first Platform Admin (made
    before they ever logged in), or a person whose GoTrue account was made
    again. Linking keeps the row's status and rights, so a disabled person
    stays disabled. Anyone else gets a new row with no rights.
    """
    user = await _by_auth_user_id(db, claims.auth_user_id)
    if user is not None:
        return user

    user = await _by_email(db, claims.email)
    if user is None:
        user = User(email=normalize_email(claims.email))
        db.add(user)
    user.auth_user_id = claims.auth_user_id
    try:
        await db.commit()
    except IntegrityError:
        # Two first requests at once: the other one created or linked the row.
        await db.rollback()
        existing = await _by_auth_user_id(db, claims.auth_user_id)
        if existing is None:
            raise
        return existing
    await db.refresh(user)
    return user


async def ensure_platform_admin(
    db: AsyncSession, gotrue: GoTrueAdmin, *, email: str, password: str
) -> User:
    """Make sure the first Platform Admin exists, in GoTrue and here.

    Runs at every startup and changes nothing the second time: an existing
    GoTrue account keeps its password (as the MVP's seeded admin did). With
    a password the account is created ready to use; without one GoTrue
    emails an invitation to choose it.
    """
    email = normalize_email(email)
    if password:
        auth_user_id = await gotrue.create_user(email, password)
    else:
        auth_user_id = await gotrue.invite_user(email)

    user = await _by_email(db, email)
    if user is None:
        user = User(email=email)
        db.add(user)
    user.is_platform_admin = True
    if auth_user_id is not None:  # a new GoTrue account: link it now
        user.auth_user_id = auth_user_id
    await db.commit()
    await db.refresh(user)
    return user


async def update_user(
    db: AsyncSession, gotrue: GoTrueAdmin, *, actor: User, user_id: uuid.UUID, data: UserUpdate
) -> User:
    """Disable or enable a person (Platform Admin).

    Disabled: every request answers 403 user_disabled from the next one on
    (we check `is_active` each time), and GoTrue bans the account, so it can
    neither log in nor refresh. The row is changed first and committed only
    after GoTrue agreed; if GoTrue fails, nothing changes (502).
    """
    user = await get_user(db, user_id)
    if data.is_active is None:  # nothing sent: nothing to change
        return user
    if user.id == actor.id and not data.is_active:
        raise ConflictError("You can't disable yourself")

    user.is_active = data.is_active
    await db.flush()  # write (and lock) the row before asking GoTrue
    if user.auth_user_id is not None:  # never logged in: no GoTrue account linked yet
        # Sent even when the value didn't change, so GoTrue always ends up in step.
        await gotrue.set_banned(user.auth_user_id, banned=not data.is_active)
    await db.commit()
    await db.refresh(user)
    return user
