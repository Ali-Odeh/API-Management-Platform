"""Request and response shapes for /api/v1/workspaces and /api/v1/me/workspaces."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from app.core.permissions import Role
from app.core.schemas import PartialUpdate, PersonEmail
from app.workspaces.models import (
    SLUG_MAX_LENGTH,
    SLUG_MIN_LENGTH,
    SLUG_PATTERN,
    STATUS_REASON_MAX_LENGTH,
    WorkspaceStatus,
)

# Names the platform keeps for itself: subdomains like api.<gateway> or
# app.<gateway> must never belong to a tenant (PLAN.md section 5).
RESERVED_SLUGS = frozenset(
    {
        "www", "api", "app", "admin", "platform", "portal", "auth", "login",
        "mail", "status", "docs", "static", "internal", "gateway", "health",
    }
)  # fmt: skip


def _not_reserved(slug: str) -> str:
    if slug in RESERVED_SLUGS:
        raise ValueError(f"'{slug}' is reserved")
    return slug


WorkspaceSlug = Annotated[
    str,
    StringConstraints(min_length=SLUG_MIN_LENGTH, max_length=SLUG_MAX_LENGTH, pattern=SLUG_PATTERN),
    AfterValidator(_not_reserved),
    Field(
        description="Lowercase letters, digits and '-' (a DNS label: it becomes the subdomain); "
        "can't be changed later",
        examples=["acme"],
    ),
]
WorkspaceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class WorkspaceCreate(BaseModel):
    name: WorkspaceName
    slug: WorkspaceSlug
    owner_email: Annotated[
        PersonEmail,
        Field(description="The first Owner; a new person gets an invitation email"),
    ]


class WorkspaceUpdate(PartialUpdate):
    """Rename; the slug never changes (it is the gateway subdomain)."""

    name: WorkspaceName | None = None


class WorkspaceDelete(BaseModel):
    confirm: Annotated[
        str, Field(description="The workspace's slug, typed again", examples=["acme"])
    ]


class Suspension(BaseModel):
    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=STATUS_REASON_MAX_LENGTH),
        Field(description="Shown to the workspace's members", examples=["Abuse report #1234"]),
    ]


# Upper bounds keep a typo (one zero too many) from meaning "no limit".
RateLimit = Annotated[int, Field(ge=1, le=1_000_000, description="Requests per minute")]
MaxConcurrent = Annotated[int, Field(ge=1, le=10_000, description="Requests in flight per gateway")]
MaxServices = Annotated[int, Field(ge=1, le=10_000)]
MaxRoutes = Annotated[int, Field(ge=1, le=100_000)]
MaxConsumers = Annotated[int, Field(ge=1, le=1_000_000)]


class Limits(BaseModel):
    """The limits as they apply: the workspace's own, or the platform default."""

    rate_limit_per_min: int
    max_concurrent: int
    max_services: int
    max_routes: int
    max_consumers: int


class LimitOverrides(BaseModel):
    """The workspace's own limits; null = the platform default."""

    rate_limit_per_min: RateLimit | None = None
    max_concurrent: MaxConcurrent | None = None
    max_services: MaxServices | None = None
    max_routes: MaxRoutes | None = None
    max_consumers: MaxConsumers | None = None


class LimitsUpdate(LimitOverrides):
    """Only the limits sent change. Unlike other PATCHes, null is allowed: it
    puts the limit back to the platform default. Lowering a quota below what
    the workspace has refuses new objects, never removes existing ones."""


class Usage(BaseModel):
    services: int
    routes: int
    consumers: int


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: WorkspaceStatus
    status_reason: str | None
    deleted_at: datetime | None
    purge_after: datetime | None = Field(
        description="A deleted workspace can be restored until then"
    )
    limits: Limits
    limit_overrides: LimitOverrides
    created_at: datetime


class WorkspaceDetail(WorkspaceOut):
    """One workspace, with how much of each quota it uses."""

    usage: Usage


class MyWorkspaceOut(WorkspaceOut):
    """A workspace I belong to, and my role there."""

    role: Role
