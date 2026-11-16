from enum import StrEnum

from sqlalchemy import CheckConstraint, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin, WorkspaceMixin

DEFAULT_RATE_LIMIT_PER_MIN = 60

# Constraint names, used by crud.py to turn violations into clear 409 errors.
UQ_CONSUMER_NAME = "uq_consumers_workspace_id_name"


class ConsumerStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"  # the gateway answers 403 for all of this consumer's keys


class Consumer(Base, UUIDPrimaryKeyMixin, TimestampMixin, WorkspaceMixin):
    """An app that calls the APIs through the gateway (plan section 5)."""

    __tablename__ = "consumers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name=UQ_CONSUMER_NAME),
        # Target for the (consumer_id, workspace_id) foreign key in api_keys,
        # which guarantees a key is always in its consumer's workspace.
        UniqueConstraint("id", "workspace_id", name="uq_consumers_id_workspace_id"),
        CheckConstraint("rate_limit_per_min > 0", name="rate_limit_positive"),
    )

    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255))
    # Stored as text with a CHECK (not a Postgres ENUM type): easier to extend later.
    status: Mapped[ConsumerStatus] = mapped_column(
        Enum(
            ConsumerStatus,
            name="status",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        default=ConsumerStatus.ACTIVE,
        server_default=ConsumerStatus.ACTIVE.value,
    )
    rate_limit_per_min: Mapped[int] = mapped_column(
        Integer,
        default=DEFAULT_RATE_LIMIT_PER_MIN,
        server_default=str(DEFAULT_RATE_LIMIT_PER_MIN),
    )
