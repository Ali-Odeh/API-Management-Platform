"""Response shapes of the portal API: what developers see (P.1)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.apis.models import ApiStatus


class CatalogWorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    apis: int  # published and deprecated APIs


class OfferedPlan(BaseModel):
    """A plan a developer may choose for an app (P.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    rate_limit_per_min: int
    monthly_quota: int | None  # none: no monthly quota
    requires_approval: bool  # an app on it waits for an API Admin


class CatalogRoute(BaseModel):
    """Where to call an API through the gateway, on the workspace's host."""

    model_config = ConfigDict(from_attributes=True)

    path_prefix: str
    methods: list[str]
    auth_required: bool  # true: send an API key (X-API-Key)


class CatalogApiSummary(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str
    status: ApiStatus  # `deprecated`: still works, takes no new apps
    has_openapi: bool
    routes: list[CatalogRoute]
    updated_at: datetime


class CatalogApi(CatalogApiSummary):
    openapi: dict[str, Any] | None
