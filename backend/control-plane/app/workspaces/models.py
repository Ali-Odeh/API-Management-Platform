from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Enum, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin

DEFAULT_WORKSPACE_SLUG = "default"
# A valid DNS label of 3 to 50 characters: the slug is the gateway subdomain (MT.7).
SLUG_PATTERN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
SLUG_MIN_LENGTH, SLUG_MAX_LENGTH = 3, 50
STATUS_REASON_MAX_LENGTH = 500


class WorkspaceStatus(StrEnum):
    """Where a workspace is in its life (decision 10)."""

    ACTIVE = "active"
    SUSPENDED = "suspended"  # by the Platform Admin: read-only, traffic refused
    DELETED = "deleted"  # hidden, restorable until purge_after
    PURGED = "purged"  # its data is gone; the row keeps the slug taken forever

    @property
    def is_gone(self) -> bool:
        """Deleted or purged: hidden from members, out of the gateway's config."""
        return self in (WorkspaceStatus.DELETED, WorkspaceStatus.PURGED)


# Columns a Platform Admin may set per workspace; NULL = the platform default.
LIMIT_COLUMNS = (
    "rate_limit_per_min",
    "max_concurrent",
    "max_services",
    "max_routes",
    "max_consumers",
)

_GONE_SQL = "status IN ('deleted', 'purged')"


class Workspace(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A tenant: its members, services, routes, consumers and keys belong to it.

    Never hard-deleted: a purged workspace keeps its row, so its slug (the
    gateway subdomain) can't go to a new tenant and reach the old one's clients.
    """

    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            f"slug ~ '{SLUG_PATTERN}' AND char_length(slug) BETWEEN "
            f"{SLUG_MIN_LENGTH} AND {SLUG_MAX_LENGTH}",
            name="slug_dns_label",
        ),
        # Requests without a subdomain go to `default`: it always serves them.
        CheckConstraint(
            f"slug <> '{DEFAULT_WORKSPACE_SLUG}' OR status = 'active'", name="default_stays_active"
        ),
        # Only a suspension has a reason; only a deleted (or purged) workspace
        # has its deletion times, and its purge comes after its deletion.
        CheckConstraint(
            "status_reason IS NULL OR status = 'suspended'", name="reason_if_suspended"
        ),
        CheckConstraint(
            f"({_GONE_SQL}) = (deleted_at IS NOT NULL) "
            f"AND ({_GONE_SQL}) = (purge_after IS NOT NULL) "
            "AND (purge_after IS NULL OR purge_after > deleted_at)",
            name="deletion_times",
        ),
        *(
            CheckConstraint(f"{column} IS NULL OR {column} > 0", name=f"{column}_positive")
            for column in LIMIT_COLUMNS
        ),
        # The purge job's question: which deleted workspaces are due?
        Index(
            "ix_workspaces_purge_after",
            "purge_after",
            postgresql_where=text("status = 'deleted'"),
        ),
    )

    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(50), unique=True)
    # Text with a CHECK (not a Postgres ENUM type), like consumers.status.
    status: Mapped[WorkspaceStatus] = mapped_column(
        Enum(
            WorkspaceStatus,
            name="status",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        default=WorkspaceStatus.ACTIVE,
        server_default=WorkspaceStatus.ACTIVE.value,
    )
    status_reason: Mapped[str | None] = mapped_column(String(STATUS_REASON_MAX_LENGTH))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Limits (decision 6); NULL = the platform default from the settings.
    rate_limit_per_min: Mapped[int | None]
    max_concurrent: Mapped[int | None]
    max_services: Mapped[int | None]
    max_routes: Mapped[int | None]
    max_consumers: Mapped[int | None]
