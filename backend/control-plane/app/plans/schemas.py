"""Request and response shapes for /api/v1/workspaces/{slug}/plans."""

import uuid
from datetime import datetime
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.schemas import PartialUpdate
from app.plans.models import MAX_MONTHLY_QUOTA, MAX_RATE_LIMIT_PER_MIN

# A display name ("Free", "Pro", "Legacy 60/min"): shown to developers later.
PlanName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$"
    ),
    Field(examples=["Pro"]),
]
RateLimit = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_RATE_LIMIT_PER_MIN,
        description="Requests per minute for each consumer on the plan, all its keys together; "
        "at most the workspace's own limit",
    ),
]
MonthlyQuota = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_MONTHLY_QUOTA,
        description="Requests per calendar month (UTC) for each consumer",
    ),
]


class PlanCreate(BaseModel):
    name: PlanName
    rate_limit_per_min: RateLimit
    monthly_quota: MonthlyQuota | None = None  # none: no monthly quota
    is_default: bool = False  # true moves the default here from the current one


class PlanUpdate(PartialUpdate):
    """`monthly_quota: null` removes the quota. `is_default: true` makes this
    plan the default; a default can't be unset, only moved."""

    nullable: ClassVar[frozenset[str]] = frozenset({"monthly_quota"})

    name: PlanName | None = None
    rate_limit_per_min: RateLimit | None = None
    monthly_quota: MonthlyQuota | None = None
    is_default: bool | None = None


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    rate_limit_per_min: int
    monthly_quota: int | None
    is_default: bool
    created_at: datetime
    updated_at: datetime
