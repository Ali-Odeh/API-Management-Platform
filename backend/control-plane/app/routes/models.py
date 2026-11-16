import uuid

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, String, false, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin, WorkspaceMixin

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

# Constraint names, used by crud.py files to turn violations into clear 409 errors.
UQ_ROUTE_PATH_PREFIX = "uq_routes_path_prefix"
FK_ROUTE_SERVICE = "fk_routes_service"

_METHODS_SQL = ", ".join(f"'{m}'" for m in HTTP_METHODS)


class Route(Base, UUIDPrimaryKeyMixin, TimestampMixin, WorkspaceMixin):
    """Maps a URL path prefix (and methods) to the service that handles it."""

    __tablename__ = "routes"
    __table_args__ = (
        # Composite FK: the route's workspace must equal its service's workspace.
        # RESTRICT: a service that still has routes can't be deleted.
        ForeignKeyConstraint(
            ["service_id", "workspace_id"],
            ["services.id", "services.workspace_id"],
            name=FK_ROUTE_SERVICE,
            ondelete="RESTRICT",
        ),
        # Safety net under the API's validation (step 2.2):
        CheckConstraint("path_prefix LIKE '/%'", name="path_prefix_leading_slash"),
        CheckConstraint(
            "path_prefix = '/' OR path_prefix NOT LIKE '%/'", name="path_prefix_no_trailing_slash"
        ),
        CheckConstraint("cardinality(methods) > 0", name="methods_not_empty"),
        CheckConstraint(f"methods <@ ARRAY[{_METHODS_SQL}]::varchar[]", name="methods_known"),
    )

    service_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # Unique across ALL workspaces in the MVP: the gateway matches by path only.
    # Milestone MT makes it unique per workspace (PLAN.md section 5).
    path_prefix: Mapped[str] = mapped_column(String(255), unique=True)
    methods: Mapped[list[str]] = mapped_column(ARRAY(String(10)))
    strip_prefix: Mapped[bool] = mapped_column(default=False, server_default=false())
    # Secure by default: a new route needs an API key unless turned off.
    auth_required: Mapped[bool] = mapped_column(default=True, server_default=true())
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
