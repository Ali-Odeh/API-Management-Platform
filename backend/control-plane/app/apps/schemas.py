"""Request and response shapes for apps: the developers' (portal) and the members' views."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.apis.models import ApiStatus
from app.apps.models import NAME_MAX_LENGTH, AppStatus
from app.core.schemas import PartialUpdate
from app.usage.schemas import TrafficOut

AppName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=NAME_MAX_LENGTH,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    ),
    Field(examples=["My shop's backend"]),
]


class AppCreate(BaseModel):
    """A new app in a workspace, for one of its published APIs, on one of the
    plans it offers developers."""

    workspace: str = Field(description="The workspace's slug", examples=["acme"])
    api_id: uuid.UUID
    plan_id: uuid.UUID
    name: AppName


class Decision(StrEnum):
    """What an API Admin can decide about an app."""

    APPROVED = "approved"
    REJECTED = "rejected"


class AppDecision(PartialUpdate):
    """PATCH /workspaces/{slug}/apps/{id}: approve (its keys work) or reject
    (they stop working). Either can be changed again later."""

    status: Decision | None = None


class WorkspaceRef(BaseModel):
    slug: str
    name: str


class ApiRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    status: ApiStatus


class PlanRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    rate_limit_per_min: int
    monthly_quota: int | None


class AppOut(BaseModel):
    """An app as its developer sees it."""

    id: uuid.UUID
    name: str
    status: AppStatus  # approved: its keys work
    workspace: WorkspaceRef
    api: ApiRef
    plan: PlanRef
    created_at: datetime


class AppAdminOut(BaseModel):
    """An app as the workspace's members see it: also who made it and its consumer."""

    id: uuid.UUID
    name: str
    status: AppStatus
    developer_email: str
    consumer_id: uuid.UUID
    api: ApiRef
    plan: PlanRef
    created_at: datetime
    updated_at: datetime


class Quota(BaseModel):
    """This calendar month (UTC), counted from the request log: requests the
    gateway let through on the app's keys. The gateway's own counter decides;
    this can trail it by the few seconds the log takes, or miss events lost
    while Redis was down."""

    monthly_quota: int | None  # none: no monthly quota on the plan
    used: int
    remaining: int | None
    resets_at: datetime  # the start of the next month, UTC


class AppUsage(BaseModel):
    traffic: TrafficOut
    quota: Quota
