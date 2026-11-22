import uuid
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin, WorkspaceMixin

DEFAULT_TIMEOUT_MS = 5000

# Load balancing and health checks (decision 14), the plan's defaults.
LB_ROUND_ROBIN: Final = "round_robin"
DEFAULT_HC_PATH = "/health"
DEFAULT_HC_INTERVAL_S = 10
DEFAULT_HC_UNHEALTHY_THRESHOLD = 3
DEFAULT_HC_HEALTHY_THRESHOLD = 2
DEFAULT_PASSIVE_FAILURES = 5
DEFAULT_PASSIVE_EJECT_S = 30
DEFAULT_TARGET_WEIGHT = 100

# Constraint names, used by crud.py to turn violations into clear 409 errors.
UQ_SERVICE_NAME = "uq_services_workspace_id_name"
UQ_TARGET_URL = "uq_service_targets_service_id_url"


class Service(Base, UUIDPrimaryKeyMixin, TimestampMixin, WorkspaceMixin):
    """One backend API the gateway can forward requests to, on one or more targets."""

    __tablename__ = "services"
    __table_args__ = (
        # Unique per workspace (option C): two workspaces may both have "orders".
        UniqueConstraint("workspace_id", "name", name=UQ_SERVICE_NAME),
        # Target for the (service_id, workspace_id) foreign keys in routes and
        # service_targets, which keep them in their service's workspace.
        UniqueConstraint("id", "workspace_id", name="uq_services_id_workspace_id"),
        CheckConstraint(f"lb_algorithm IN ('{LB_ROUND_ROBIN}')", name="lb_algorithm"),
        CheckConstraint("hc_path LIKE '/%'", name="hc_path"),
        CheckConstraint("hc_interval_s BETWEEN 1 AND 300", name="hc_interval_s"),
        CheckConstraint("hc_unhealthy_threshold BETWEEN 1 AND 10", name="hc_unhealthy_threshold"),
        CheckConstraint("hc_healthy_threshold BETWEEN 1 AND 10", name="hc_healthy_threshold"),
        CheckConstraint("passive_failures BETWEEN 1 AND 100", name="passive_failures"),
        CheckConstraint("passive_eject_s BETWEEN 1 AND 3600", name="passive_eject_s"),
    )

    name: Mapped[str] = mapped_column(String(100))
    timeout_ms: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_TIMEOUT_MS, server_default=str(DEFAULT_TIMEOUT_MS)
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    lb_algorithm: Mapped[str] = mapped_column(
        String(20), default=LB_ROUND_ROBIN, server_default=LB_ROUND_ROBIN
    )
    hc_path: Mapped[str] = mapped_column(
        String(255), default=DEFAULT_HC_PATH, server_default=DEFAULT_HC_PATH
    )
    hc_interval_s: Mapped[int] = mapped_column(
        default=DEFAULT_HC_INTERVAL_S, server_default=str(DEFAULT_HC_INTERVAL_S)
    )
    hc_unhealthy_threshold: Mapped[int] = mapped_column(
        default=DEFAULT_HC_UNHEALTHY_THRESHOLD, server_default=str(DEFAULT_HC_UNHEALTHY_THRESHOLD)
    )
    hc_healthy_threshold: Mapped[int] = mapped_column(
        default=DEFAULT_HC_HEALTHY_THRESHOLD, server_default=str(DEFAULT_HC_HEALTHY_THRESHOLD)
    )
    passive_failures: Mapped[int] = mapped_column(
        default=DEFAULT_PASSIVE_FAILURES, server_default=str(DEFAULT_PASSIVE_FAILURES)
    )
    passive_eject_s: Mapped[int] = mapped_column(
        default=DEFAULT_PASSIVE_EJECT_S, server_default=str(DEFAULT_PASSIVE_EJECT_S)
    )

    # Loaded with the service (selectin: one extra query for a whole page of
    # services), oldest first, so answers list targets in a stable order.
    targets: Mapped[list["ServiceTarget"]] = relationship(
        lazy="selectin",
        order_by="(ServiceTarget.created_at, ServiceTarget.id)",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ServiceTarget(Base, UUIDPrimaryKeyMixin, TimestampMixin, WorkspaceMixin):
    """One backend instance of a service (decision 14): the gateway spreads
    requests over a service's active, healthy targets by weight."""

    __tablename__ = "service_targets"
    __table_args__ = (
        # Composite: a target always lives in its service's workspace.
        ForeignKeyConstraint(
            ["service_id", "workspace_id"],
            ["services.id", "services.workspace_id"],
            name="fk_service_targets_service",
            ondelete="CASCADE",
        ),
        UniqueConstraint("service_id", "url", name=UQ_TARGET_URL),
        CheckConstraint("weight BETWEEN 1 AND 1000", name="weight"),
    )

    service_id: Mapped[uuid.UUID] = mapped_column(index=True)
    url: Mapped[str] = mapped_column(String(2048))
    weight: Mapped[int] = mapped_column(
        default=DEFAULT_TARGET_WEIGHT, server_default=str(DEFAULT_TARGET_WEIGHT)
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
