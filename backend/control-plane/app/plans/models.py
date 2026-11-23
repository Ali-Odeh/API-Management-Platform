"""Plans: a workspace's tiers of access (V2.2).

A plan sets a rate limit and, optionally, a monthly quota. Every consumer is
on exactly one plan of its own workspace, and every workspace has exactly
one default plan, which new consumers get when none is named. The
workspace's own limit (set by the Platform Admin) stays the ceiling above
all of its plans.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin, WorkspaceMixin

MAX_RATE_LIMIT_PER_MIN = 100_000
MAX_MONTHLY_QUOTA = 10**12

# The plan every workspace starts with: the MVP's consumer default.
DEFAULT_PLAN_NAME = "Default"
DEFAULT_PLAN_RATE_LIMIT_PER_MIN = 60

# Constraint names, used by crud.py to turn violations into clear errors.
UQ_PLAN_NAME = "uq_plans_workspace_id_name"
UQ_ONE_DEFAULT = "uq_plans_one_default_per_workspace"


class Plan(Base, UUIDPrimaryKeyMixin, TimestampMixin, WorkspaceMixin):
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name=UQ_PLAN_NAME),
        # Target of consumers' (plan_id, workspace_id) foreign key: a consumer's
        # plan is always in its own workspace.
        UniqueConstraint("id", "workspace_id", name="uq_plans_id_workspace_id"),
        Index(UQ_ONE_DEFAULT, "workspace_id", unique=True, postgresql_where=text("is_default")),
        CheckConstraint(
            f"rate_limit_per_min BETWEEN 1 AND {MAX_RATE_LIMIT_PER_MIN}", name="rate_limit_range"
        ),
        CheckConstraint(
            f"monthly_quota IS NULL OR monthly_quota BETWEEN 1 AND {MAX_MONTHLY_QUOTA}",
            name="monthly_quota_range",
        ),
    )

    name: Mapped[str] = mapped_column(String(100))
    rate_limit_per_min: Mapped[int] = mapped_column(Integer)
    monthly_quota: Mapped[int | None] = mapped_column(BigInteger)  # NULL: no monthly quota
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
