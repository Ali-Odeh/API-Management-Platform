import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin

# Constraint names, used by crud.py to recognize a race on the first request.
UQ_DEVELOPER_AUTH_USER_ID = "uq_developers_auth_user_id"
UQ_DEVELOPER_EMAIL = "uq_developers_email"


class DeveloperStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"  # every portal request answers 403 developer_blocked


class Developer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Someone who signed up on the developer portal (P.1).

    Never a workspace member and never in `users`: developers sign up on
    their own GoTrue (`auth-portal`), so open sign-up can't reach a member's
    account. Created just in time on their first portal request and bound to
    that GoTrue account for good. Not tenant data: a developer browses every
    workspace's catalog; their apps (P.2) belong to workspaces.
    """

    __tablename__ = "developers"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    auth_user_id: Mapped[uuid.UUID] = mapped_column(unique=True)  # the portal GoTrue's `sub`
    email: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[DeveloperStatus] = mapped_column(
        Enum(
            DeveloperStatus,
            name="status",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        default=DeveloperStatus.ACTIVE,
        server_default=DeveloperStatus.ACTIVE.value,
    )
