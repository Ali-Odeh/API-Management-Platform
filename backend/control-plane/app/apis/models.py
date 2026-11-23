"""APIs: what a workspace shows developers in its catalog (P.1).

An API groups some of the workspace's routes under a name, with an optional
OpenAPI document, and has a lifecycle: `draft` (members only), `published`
(in the catalog), `deprecated` (still in the catalog, marked, and taking no
new apps from P.2 on). APIs don't change the gateway's config: routes work
the same whether an API includes them or not.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin, WorkspaceMixin

NAME_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 2000
# The document's size as JSON without spaces, checked by the API. Postgres
# prints jsonb with a space after every ':' and ',', so its own CHECK (a
# safety net) allows twice as much.
OPENAPI_MAX_BYTES = 1024 * 1024
_OPENAPI_MAX_STORED_BYTES = 2 * OPENAPI_MAX_BYTES

# Constraint names, used by crud.py to turn violations into clear errors.
UQ_API_SLUG = "uq_apis_workspace_id_slug"
PK_API_ROUTES = "pk_api_routes"  # a route belongs to at most one API


class ApiStatus(StrEnum):
    DRAFT = "draft"  # members only; the portal answers 404
    PUBLISHED = "published"
    DEPRECATED = "deprecated"  # still shown, marked; takes no new apps (P.2)

    @property
    def in_catalog(self) -> bool:
        return self is not ApiStatus.DRAFT


class Api(Base, UUIDPrimaryKeyMixin, TimestampMixin, WorkspaceMixin):
    __tablename__ = "apis"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name=UQ_API_SLUG),
        # Target of api_routes' (api_id, workspace_id) foreign key.
        UniqueConstraint("id", "workspace_id", name="uq_apis_id_workspace_id"),
        CheckConstraint(
            "openapi IS NULL OR (jsonb_typeof(openapi) = 'object' "
            f"AND octet_length(openapi::text) <= {_OPENAPI_MAX_STORED_BYTES})",
            name="openapi_object_within_size",
        ),
    )

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    slug: Mapped[str] = mapped_column(String(100))  # never changes once made
    description: Mapped[str] = mapped_column(
        String(DESCRIPTION_MAX_LENGTH), default="", server_default=""
    )
    status: Mapped[ApiStatus] = mapped_column(
        Enum(
            ApiStatus,
            name="status",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        default=ApiStatus.DRAFT,
        server_default=ApiStatus.DRAFT.value,
    )
    # none_as_null: None is SQL NULL (no document), not the JSON value `null`.
    openapi: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)  # type: ignore[no-untyped-call]  # untyped in SQLAlchemy
    )


class ApiRoute(Base, WorkspaceMixin):
    """A route that is part of an API. Keyed by the route: one API at most.

    Both foreign keys include the workspace, so an API and its routes are
    always in one workspace. Deleting the API or the route removes the link.
    """

    __tablename__ = "api_routes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["api_id", "workspace_id"],
            ["apis.id", "apis.workspace_id"],
            name="fk_api_routes_api",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["route_id", "workspace_id"],
            ["routes.id", "routes.workspace_id"],
            name="fk_api_routes_route",
            ondelete="CASCADE",
        ),
    )

    route_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    api_id: Mapped[uuid.UUID] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
