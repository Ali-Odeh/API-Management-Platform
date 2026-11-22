"""The config sequence and the per-workspace config versions (decision 7).

Nothing in Python bumps them. Postgres triggers (see the migration
"incremental config sequence") run after every statement on the tables in
CONFIG_COLUMNS, find the workspaces whose gateway config changed, and in the
same transaction:
- add 1 to the global `config_seq`,
- add 1 to each of those workspaces' `workspace_config.version` and set
  their `workspace_config.seq` to the new sequence,
- send `NOTIFY config_changed, '<new seq>'`.
So no code path can forget it, cascades are covered, and a failed
transaction bumps and notifies nothing. The gateway asks "what changed after
seq N?" and gets exactly the workspaces with `workspace_config.seq > N`.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, SmallInteger, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# The tables the gateway's config is built from, with the columns that are
# part of it. Only a change to one of these columns bumps: renaming a
# consumer or a workspace, or `updated_at` / `last_used_at` moving, doesn't.
CONFIG_COLUMNS: dict[str, tuple[str, ...]] = {
    "services": (
        "timeout_ms",
        "is_active",
        "lb_algorithm",
        "hc_path",
        "hc_interval_s",
        "hc_unhealthy_threshold",
        "hc_healthy_threshold",
        "passive_failures",
        "passive_eject_s",
    ),
    "service_targets": ("service_id", "url", "weight", "is_active"),
    "routes": (
        "service_id",
        "path_prefix",
        "methods",
        "strip_prefix",
        "auth_required",
        "is_active",
        "max_body_bytes",
        "cors_origins",
        "cors_methods",
        "cors_headers",
        "cors_credentials",
        "cors_max_age",
    ),
    "consumers": ("status", "rate_limit_per_min"),
    "api_keys": ("consumer_id", "key_hash", "revoked_at"),
    "workspaces": ("slug", "status", "rate_limit_per_min", "max_concurrent"),
}

# The Postgres NOTIFY channel the triggers send to.
CONFIG_CHANGED_CHANNEL = "config_changed"

SINGLETON_ID = 1


class ConfigSeq(Base):
    """The global sequence: the one number a gateway remembers.

    A single-row table (system state, so no uuid and no workspace_id).
    """

    __tablename__ = "config_seq"
    __table_args__ = (CheckConstraint(f"id = {SINGLETON_ID}", name="single_row"),)

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=False, default=SINGLETON_ID
    )
    seq: Mapped[int] = mapped_column(BigInteger, server_default="1")
    generation: Mapped[uuid.UUID] = mapped_column(server_default=func.gen_random_uuid())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkspaceConfig(Base):
    """One workspace's config version, and the sequence of its last change.

    Made by the trigger when the workspace is inserted. Kept when the
    workspace is purged: a gateway that was away still learns it is gone.
    """

    __tablename__ = "workspace_config"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), primary_key=True
    )
    version: Mapped[int] = mapped_column(BigInteger, server_default="1")
    seq: Mapped[int] = mapped_column(BigInteger, index=True)
