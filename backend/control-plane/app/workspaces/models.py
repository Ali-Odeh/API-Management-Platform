from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin

DEFAULT_WORKSPACE_SLUG = "default"


class Workspace(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A tenant. The MVP has exactly one: `default`."""

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(50), unique=True)
