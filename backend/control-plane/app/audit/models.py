import uuid
from datetime import datetime
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import UUIDPrimaryKeyMixin


class ActorType(StrEnum):
    USER = "user"  # a person acting within their own role
    PLATFORM_ADMIN = "platform_admin"  # acting by platform power (or on a platform endpoint)
    SYSTEM = "system"  # startup, scripts and jobs


_ACTOR_TYPES_SQL = ", ".join(f"'{t}'" for t in ActorType)


class AuditEvent(Base, UUIDPrimaryKeyMixin):
    """Who changed what, and when (decision 11). Append-only.

    The API's login may only INSERT and SELECT here; only the retention job
    deletes. No foreign keys and copied names: an event stays readable after
    its actor, target or workspace is gone. No updated_at: events never change.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(f"actor_type IN ({_ACTOR_TYPES_SQL})", name="actor_type_known"),
        Index("ix_audit_events_workspace_id_created_at", "workspace_id", "created_at"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # NULL: a platform-level event (a workspace created, a user disabled).
    workspace_id: Mapped[uuid.UUID | None]
    actor_user_id: Mapped[uuid.UUID | None]  # NULL for system events
    actor_email: Mapped[str | None] = mapped_column(String(255))
    actor_type: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(100))  # e.g. service.create, key.revoke
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[uuid.UUID | None]
    target_name: Mapped[str | None] = mapped_column(String(255))
    # Changed fields only, {"timeout_ms": [5000, 10000]}; never a secret.
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(128))
    ip: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(512))
