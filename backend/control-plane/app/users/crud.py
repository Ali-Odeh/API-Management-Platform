"""All database work for users, plus the GoTrue calls that go with it."""

import dataclasses
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import crud as audit
from app.audit.changes import Changes, created, snapshot, updated
from app.audit.context import AuditContext
from app.core.db import flush_or_raise
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableError,
    UpstreamError,
)
from app.core.identity import TokenClaims
from app.core.pagination import PageParams, paginate
from app.gotrue.client import GoTrueAdmin
from app.members.models import WorkspaceMember
from app.users.models import UQ_USER_AUTH_USER_ID, UQ_USER_EMAIL, User
from app.users.schemas import Membership, UserUpdate
from app.workspaces.models import Workspace

logger = logging.getLogger(__name__)

# What a person's audit events record. User changes are platform-level
# events (workspace_id NULL).
AUDIT_FIELDS = ("email", "auth_user_id", "is_platform_admin", "is_active")

# The token's GoTrue account isn't the one this email belongs to here.
ACCOUNT_CONFLICT = "account_conflict"
DISABLED_NOT_BANNED = (
    "Disabled here, but the auth service didn't confirm the ban. Repeat the request to finish it."
)


def normalize_email(email: str) -> str:
    """'  Admin@Example.COM ' and 'admin@example.com' are the same person."""
    return email.strip().lower()


async def get_user(db: AsyncSession, user_id: uuid.UUID, *, lock: bool = False) -> User:
    """`lock`: SELECT ... FOR UPDATE, so parallel changes to this person wait their turn."""
    user = await db.get(User, user_id, with_for_update=lock, populate_existing=lock)
    if user is None:
        raise NotFoundError("User not found")
    return user


async def list_users(
    db: AsyncSession,
    *,
    page: PageParams,
    is_active: bool | None = None,
    email: str | None = None,
) -> tuple[list[User], int]:
    """One page of everyone, newest first; `email` matches part of the address."""
    query = select(User).order_by(User.created_at.desc(), User.id)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    if email:
        query = query.where(User.email.contains(normalize_email(email), autoescape=True))
    return await paginate(db, query, page)


async def memberships_of(
    db: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[Membership]]:
    """The workspaces each of these people belongs to, by name. One query for the page."""
    rows = await db.execute(
        select(
            WorkspaceMember.user_id,
            Workspace.slug,
            Workspace.name,
            WorkspaceMember.role,
            Workspace.status,
        )
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id.in_(user_ids))
        .order_by(Workspace.name, Workspace.slug)
    )
    found: dict[uuid.UUID, list[Membership]] = {user_id: [] for user_id in user_ids}
    for user_id, slug, name, role, status in rows:
        found[user_id].append(Membership(slug=slug, name=name, role=role, status=status))
    return found


async def _by_auth_user_id(db: AsyncSession, auth_user_id: uuid.UUID) -> User | None:
    result = await db.scalars(select(User).where(User.auth_user_id == auth_user_id))
    return result.one_or_none()


async def _by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.scalars(select(User).where(User.email == normalize_email(email)))
    return result.one_or_none()


async def get_or_invite(db: AsyncSession, gotrue: GoTrueAdmin, email: str) -> tuple[User, bool]:
    """The active person with this email, made a user (and invited) if new.

    Returns (user, invited). A known person is share-locked until the
    transaction ends, so a parallel disable can't slip in before the caller
    commits (it waits, or it was first and this answers 422). A new person
    gets a GoTrue account found or invited through the trusted admin API, and
    a row bound to it now: they are whatever the caller makes them (member,
    owner) from their first login. No event of its own: the caller's event
    (member.add, workspace.created) describes it.
    """
    user = await get_by_email_locked(db, email)
    if user is not None:
        if not user.is_active:
            raise UnprocessableError("This user is disabled")
        return user, False

    email = normalize_email(email)
    auth_user_id = await gotrue.find_user_by_email(email)
    invited = False
    if auth_user_id is None:
        auth_user_id = await gotrue.invite_user(email)
        invited = auth_user_id is not None
    if auth_user_id is None:  # the account was made elsewhere just now: no email from us
        auth_user_id = await gotrue.find_user_by_email(email)
    if auth_user_id is None:
        raise UpstreamError("Auth service unavailable")
    user = User(
        id=uuid.uuid4(),
        email=email,
        auth_user_id=auth_user_id,
        is_platform_admin=False,
        is_active=True,
    )
    db.add(user)
    # Two parallel adds of the same new person: the second waits, then sees it
    # taken (Postgres may report the email or the GoTrue id first). The GoTrue
    # id alone is also taken when that account's email changed and our row
    # still holds the old one.
    await flush_or_raise(
        db,
        {
            UQ_USER_EMAIL: ConflictError("This person is being added right now; try again"),
            UQ_USER_AUTH_USER_ID: ConflictError(
                "This person's account is already linked to a user (being added right now, "
                "or under another email)"
            ),
        },
    )
    return user, invited


async def get_by_email_locked(db: AsyncSession, email: str) -> User | None:
    """The person with this email (any case), share-locked until the transaction ends.

    SELECT ... FOR SHARE: nobody can change the row meanwhile (disabling
    locks it FOR UPDATE and waits), while others that only need it unchanged
    don't wait.
    """
    query = select(User).where(User.email == normalize_email(email)).with_for_update(read=True)
    return (await db.scalars(query)).one_or_none()


async def get_or_create_from_token(
    db: AsyncSession, claims: TokenClaims, *, audit: AuditContext
) -> User:
    """The person behind a verified GoTrue token; their row is created just in time.

    A row belongs to exactly one GoTrue account, found by its id (`sub`),
    never by email: a token's email only proves what GoTrue holds now, so
    another account with the same address must not inherit someone's rights.
    Rows made before their first login (the migrated admin, the first
    Platform Admin) are linked at startup through GoTrue's admin API instead.
    A new account whose email a row already holds gets 403 account_conflict.
    The new row is audited as `user.create`, by the person themself.
    """
    user = await _by_auth_user_id(db, claims.auth_user_id)
    if user is not None:
        return user
    same_email = await _by_email(db, claims.email)
    if same_email is not None:
        if same_email.auth_user_id == claims.auth_user_id:  # a parallel first request made it
            return same_email
        raise ForbiddenError(ACCOUNT_CONFLICT)

    user = User(
        id=uuid.uuid4(),
        email=normalize_email(claims.email),
        auth_user_id=claims.auth_user_id,
        is_platform_admin=False,
        is_active=True,
    )
    db.add(user)
    by_themself = dataclasses.replace(audit, actor_user_id=user.id, actor_email=user.email)
    _record(db, by_themself, user, "user.create", created(snapshot(user, AUDIT_FIELDS)))
    try:
        await db.commit()
    except IntegrityError:
        # Two first requests at once: the other one may have created the row.
        await db.rollback()
        existing = await _by_auth_user_id(db, claims.auth_user_id)
        if existing is None:  # another account took the email in the meantime
            raise ForbiddenError(ACCOUNT_CONFLICT) from None
        return existing
    await db.refresh(user)
    return user


async def _resolve_gotrue_account(gotrue: GoTrueAdmin, email: str, password: str) -> uuid.UUID:
    """The id of the GoTrue account for `email`, created (or invited) only if missing.

    Looking first means a restart never resends a pending invitation.
    """
    auth_user_id = await gotrue.find_user_by_email(email)
    if auth_user_id is not None:
        return auth_user_id
    if password:
        auth_user_id = await gotrue.create_user(email, password)
    else:
        auth_user_id = await gotrue.invite_user(email)
    if auth_user_id is None:  # made by someone else just now
        auth_user_id = await gotrue.find_user_by_email(email)
    if auth_user_id is None:
        raise UpstreamError("Auth service unavailable")
    return auth_user_id


async def ensure_platform_admin(
    db: AsyncSession, gotrue: GoTrueAdmin, *, email: str, password: str
) -> User:
    """Make sure the first Platform Admin exists, in GoTrue and here.

    Runs at every startup. A row that is already bound to a GoTrue account
    stays bound to it: startup only checks that GoTrue still gives that
    email the same id before granting admin rights, and logs a conflict if
    it differs (see `relink_user` for the deliberate fix). Only a row without
    a GoTrue id (the migrated MVP admin, or a new one) is linked automatically
    to the account found through the trusted admin API. It is created (with
    the password) or invited (without one) only when missing. A restart never
    resends a pending invitation, and an existing account keeps its password.
    """
    email = normalize_email(email)
    user = await _by_email(db, email)
    is_new = user is None
    if user is None:
        user = User(id=uuid.uuid4(), email=email, is_platform_admin=False, is_active=True)
        db.add(user)
    else:
        user = await get_user(db, user.id, lock=True)
    before = snapshot(user, AUDIT_FIELDS)

    found: uuid.UUID | None
    if user.auth_user_id is None:
        found = await _resolve_gotrue_account(gotrue, email, password)
        user.auth_user_id = found
    else:
        found = await gotrue.find_user_by_email(email)
    if found == user.auth_user_id:
        user.is_platform_admin = True
    else:
        logger.error(
            "Platform Admin %s is bound to GoTrue account %s, but GoTrue gives that "
            "email %s. The binding and privileges are kept. If the new account is "
            "right, rebind on purpose: "
            "docker compose exec control-plane python -m scripts.relink_user %s",
            email,
            user.auth_user_id,
            found or "no account",
            email,
        )
    after = snapshot(user, AUDIT_FIELDS)
    if is_new:
        _record(db, AuditContext.system(), user, "user.create", created(after))
    elif changes := updated(before, after):
        _record(db, AuditContext.system(), user, "user.update", changes)
    await db.commit()
    await db.refresh(user)
    return user


async def relink_user(
    db: AsyncSession, gotrue: GoTrueAdmin, *, email: str, audit: AuditContext
) -> tuple[uuid.UUID | None, uuid.UUID]:
    """Bind a person's row to the GoTrue account that holds their email now.

    The explicit recovery for a binding that no longer fits (e.g. GoTrue's
    database was reset and the account made again). Never automatic: an
    operator runs it on purpose (scripts/relink_user.py). The account is
    found through the trusted admin API by exact email. For a disabled user,
    ban the replacement before saving its binding. Returns (old, new).
    """
    user = await _by_email(db, email)
    if user is None:
        raise NotFoundError("User not found")
    user = await get_user(db, user.id, lock=True)
    found = await gotrue.find_user_by_email(user.email)
    if found is None:
        raise NotFoundError(f"GoTrue has no account for {user.email}")
    taken = await _by_auth_user_id(db, found)
    if taken is not None and taken.id != user.id:
        raise ConflictError(f"That GoTrue account is already bound to {taken.email}")
    old = user.auth_user_id
    if old != found and not user.is_active and not await gotrue.set_banned(found, banned=True):
        raise NotFoundError(f"GoTrue has no account for {user.email}")
    user.auth_user_id = found
    if old != found:
        _record(db, audit, user, "user.relink", {"auth_user_id": [_str(old), str(found)]})
    await db.commit()
    return old, found


async def update_user(
    db: AsyncSession,
    gotrue: GoTrueAdmin,
    *,
    actor: User,
    user_id: uuid.UUID,
    data: UserUpdate,
    audit: AuditContext,
) -> User:
    """Disable or enable a person (Platform Admin).

    Disabled: every request answers 403 user_disabled from the next one on,
    and GoTrue bans the account, so it can neither log in nor refresh.

    The row stays locked until the end, so parallel changes to one person
    run one after the other and GoTrue ends in the same state as the row.
    When any GoTrue call fails (finding the account or banning it, or a
    timeout, maybe after GoTrue acted), the row keeps the safe side: a
    disable is kept (the API is closed at once; retrying finishes the ban),
    an enable is not made (retrying finishes it).
    """
    user = await get_user(db, user_id, lock=True)
    if data.is_active is None:  # nothing sent: nothing to change
        await db.commit()  # nothing written; just ends the transaction and its lock
        return user
    if user.id == actor.id and not data.is_active:
        raise ConflictError("You can't disable yourself")

    before = snapshot(user, AUDIT_FIELDS)
    action = "user.enable" if data.is_active else "user.disable"
    if not data.is_active:
        user.is_active = False
    try:
        auth_user_id = await _linked_account(gotrue, user)
        if auth_user_id is not None:
            # Sent even when nothing changed, so a retry brings GoTrue in step.
            await gotrue.set_banned(auth_user_id, banned=not data.is_active)
    except UpstreamError:
        if data.is_active:
            raise  # not enabled; the row rolls back
        _record_if_changed(db, audit, user, action, before)
        await db.commit()  # keep the disable (and its event): the API is closed already
        raise UpstreamError(DISABLED_NOT_BANNED) from None
    user.is_active = data.is_active
    _record_if_changed(db, audit, user, action, before)
    await db.commit()
    await db.refresh(user)
    return user


async def _linked_account(gotrue: GoTrueAdmin, user: User) -> uuid.UUID | None:
    """The person's GoTrue account; one not linked yet is found by its exact email
    through the trusted admin API (and linked), so a ban reaches it too."""
    if user.auth_user_id is None:
        user.auth_user_id = await gotrue.find_user_by_email(user.email)
    return user.auth_user_id


def _record(
    db: AsyncSession, audit_ctx: AuditContext, user: User, action: str, changes: Changes
) -> None:
    audit.record(
        db,
        audit_ctx,
        workspace_id=None,
        action=action,
        target_type="user",
        target_id=user.id,
        target_name=user.email,
        changes=changes,
    )


def _record_if_changed(
    db: AsyncSession, audit_ctx: AuditContext, user: User, action: str, before: dict[str, object]
) -> None:
    """A repeated disable or enable changes nothing, so it writes no event."""
    if changes := updated(before, snapshot(user, AUDIT_FIELDS)):
        _record(db, audit_ctx, user, action, changes)


def _str(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None
