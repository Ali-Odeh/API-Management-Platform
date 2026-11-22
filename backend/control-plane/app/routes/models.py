import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin, WorkspaceMixin

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

# Constraint names, used by crud.py files to turn violations into clear 409 errors.
UQ_ROUTE_PATH_PREFIX = "uq_routes_workspace_id_path_prefix"
FK_ROUTE_SERVICE = "fk_routes_service"

_METHODS_SQL = ", ".join(f"'{m}'" for m in HTTP_METHODS)

# CORS (decision 15): how long a browser may cache a preflight answer.
DEFAULT_CORS_MAX_AGE = 600
MAX_CORS_MAX_AGE = 86_400  # browsers cap it anyway (Chrome: 2 h, Firefox: 24 h)


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
        # Unique per workspace: two workspaces may both publish /v1/orders;
        # the gateway tells them apart by Host (MT.7).
        UniqueConstraint("workspace_id", "path_prefix", name=UQ_ROUTE_PATH_PREFIX),
        CheckConstraint("cardinality(methods) > 0", name="methods_not_empty"),
        CheckConstraint(f"methods <@ ARRAY[{_METHODS_SQL}]::varchar[]", name="methods_known"),
        CheckConstraint("max_body_bytes IS NULL OR max_body_bytes > 0", name="max_body_bytes"),
        CheckConstraint(
            f"cors_methods <@ ARRAY[{_METHODS_SQL}]::varchar[]", name="cors_methods_known"
        ),
        CheckConstraint(f"cors_max_age BETWEEN 0 AND {MAX_CORS_MAX_AGE}", name="cors_max_age"),
        # A wildcard origin can't go with credentials: browsers refuse that answer.
        CheckConstraint(
            "NOT ('*' = ANY(cors_origins) AND cors_credentials)",
            name="cors_no_wildcard_credentials",
        ),
    )

    service_id: Mapped[uuid.UUID] = mapped_column(index=True)
    path_prefix: Mapped[str] = mapped_column(String(255))
    methods: Mapped[list[str]] = mapped_column(ARRAY(String(10)))
    strip_prefix: Mapped[bool] = mapped_column(default=False, server_default=false())
    # Secure by default: a new route needs an API key unless turned off.
    auth_required: Mapped[bool] = mapped_column(default=True, server_default=true())
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    # The largest request body the gateway lets through to the service (MT.11);
    # NULL = the platform default (MAX_REQUEST_BODY).
    max_body_bytes: Mapped[int | None] = mapped_column(BigInteger)
    # CORS (MT.11): off while cors_origins is empty.
    cors_origins: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), default=list, server_default="{}"
    )
    # Empty: the route's own methods.
    cors_methods: Mapped[list[str]] = mapped_column(
        ARRAY(String(10)), default=list, server_default="{}"
    )
    cors_headers: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), default=list, server_default="{}"
    )
    cors_credentials: Mapped[bool] = mapped_column(default=False, server_default=false())
    cors_max_age: Mapped[int] = mapped_column(
        default=DEFAULT_CORS_MAX_AGE, server_default=str(DEFAULT_CORS_MAX_AGE)
    )

    @property
    def cors(self) -> dict[str, Any]:
        """The CORS settings as one object, the shape the API shows."""
        return {
            "origins": self.cors_origins,
            "methods": self.cors_methods,
            "headers": self.cors_headers,
            "credentials": self.cors_credentials,
            "max_age": self.cors_max_age,
        }
