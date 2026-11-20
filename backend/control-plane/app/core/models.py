"""Building blocks shared by every table.

The plan says every table has a uuid primary key plus created_at and
updated_at. Instead of repeating those 3 columns in every model, each model
inherits them from these mixins (a mixin = a small class you "mix in" to
add fields):

    class Service(Base, UUIDPrimaryKeyMixin, TimestampMixin, WorkspaceMixin):
        ...
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, FetchedValue, ForeignKey, func
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    # server_default: Postgres fills it in on INSERT.
    # server_onupdate: the `set_updated_at` trigger sets it on every UPDATE that
    # changes the row, also for bulk statements and plain SQL, which the ORM's
    # own `onupdate` never saw. FetchedValue tells SQLAlchemy the database owns
    # the value, so crud reads it back (db.refresh) after a change.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), server_onupdate=FetchedValue()
    )


class WorkspaceMixin:
    """Tenant-ready: the record belongs to one workspace (see PLAN.md section 5)."""

    # declared_attr: a ForeignKey can't be shared between tables, so SQLAlchemy
    # calls this once per table to build a fresh column for each.
    @declared_attr
    def workspace_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            ForeignKey("workspaces.id", ondelete="RESTRICT"), index=True, nullable=False
        )
