import uuid

from sqlalchemy import CheckConstraint, String, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin

# Constraint names, used by crud.py to recognize a race on the first request.
UQ_USER_AUTH_USER_ID = "uq_users_auth_user_id"
UQ_USER_EMAIL = "uq_users_email"


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A person who can sign in to the dashboard (MT.1, replaces `admins`).

    GoTrue owns the login (password, MFA, sessions); this row holds what the
    platform decides about the person. Not tenant data: no workspace_id. A
    person reaches workspaces through memberships (MT.2).
    """

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    # GoTrue's user id (the token's `sub`). NULL only for a row made before its
    # GoTrue account was linked (the migrated admin); the first request with a
    # token for that email links it.
    auth_user_id: Mapped[uuid.UUID | None] = mapped_column(unique=True)
    # Unique and always lowercase (see crud.normalize_email).
    email: Mapped[str] = mapped_column(String(255), unique=True)
    # May do everything in every workspace, plus the platform endpoints (with MFA).
    is_platform_admin: Mapped[bool] = mapped_column(default=False, server_default=false())
    # False: every request answers 403 user_disabled, and GoTrue bans the account.
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
