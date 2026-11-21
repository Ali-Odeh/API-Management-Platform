from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin

DEFAULT_WORKSPACE_SLUG = "default"
# A valid DNS label of 3 to 50 characters: the slug is the gateway subdomain (MT.7).
SLUG_PATTERN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
SLUG_MIN_LENGTH, SLUG_MAX_LENGTH = 3, 50


class Workspace(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A tenant: its members, services, routes, consumers and keys belong to it."""

    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            f"slug ~ '{SLUG_PATTERN}' AND char_length(slug) BETWEEN "
            f"{SLUG_MIN_LENGTH} AND {SLUG_MAX_LENGTH}",
            name="slug_dns_label",
        ),
    )

    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(50), unique=True)
