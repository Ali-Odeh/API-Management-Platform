"""Request and response shapes for /api/v1/workspaces and /api/v1/me/workspaces."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.core.permissions import Role
from app.workspaces.models import SLUG_MAX_LENGTH, SLUG_MIN_LENGTH, SLUG_PATTERN

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


class WorkspaceCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    slug: WorkspaceSlug
    # Not EmailStr: it must only name an existing user (the lookup is the real
    # check), and email-validator refuses addresses GoTrue accepts (e.g. *.test).
    owner_email: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, to_lower=True, max_length=255, pattern=r"^[^@\s]+@[^@\s]+$"
        ),
        Field(description="The first Owner; they must have signed in once (invitations: MT.4)"),
    ]


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime


class MyWorkspaceOut(WorkspaceOut):
    """A workspace I belong to, and my role there."""

    role: Role
