"""The config version: one number that goes up whenever the gateway's config changes.

Nothing in Python bumps it. Postgres triggers (see the migration
"create config_version and its triggers") run after every INSERT, UPDATE or
DELETE on the tables below, add 1 to `version`, and send
`NOTIFY config_changed, '<new version>'`. So no code path can forget it,
cascades are covered, and a failed transaction bumps and notifies nothing.
"""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, SmallInteger, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Tables whose changes the gateway must see. Admins and workspaces aren't here:
# the gateway doesn't use them.
VERSIONED_TABLES = ("services", "routes", "consumers", "api_keys")

# The Postgres NOTIFY channel the triggers send to (used by step 2.5).
CONFIG_CHANGED_CHANNEL = "config_changed"

SINGLETON_ID = 1


class ConfigVersion(Base):
    """A single-row table (system state, so no uuid and no workspace_id)."""

    __tablename__ = "config_version"
    __table_args__ = (CheckConstraint(f"id = {SINGLETON_ID}", name="single_row"),)

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=False, default=SINGLETON_ID
    )
    version: Mapped[int] = mapped_column(BigInteger, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
