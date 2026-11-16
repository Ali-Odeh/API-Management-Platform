from sqlalchemy import Integer, String, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin, WorkspaceMixin

DEFAULT_TIMEOUT_MS = 5000


class Service(Base, UUIDPrimaryKeyMixin, TimestampMixin, WorkspaceMixin):
    """One backend API the gateway can forward requests to."""

    __tablename__ = "services"
    __table_args__ = (
        # Unique per workspace (option C): two workspaces may both have "orders".
        UniqueConstraint("workspace_id", "name", name="uq_services_workspace_id_name"),
    )

    name: Mapped[str] = mapped_column(String(100))
    upstream_url: Mapped[str] = mapped_column(String(2048))
    timeout_ms: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_TIMEOUT_MS, server_default=str(DEFAULT_TIMEOUT_MS)
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
