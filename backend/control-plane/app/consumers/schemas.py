"""Request and response shapes for /api/v1/consumers."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.consumers.models import ConsumerStatus
from app.core.schemas import PartialUpdate, SlugName

# A valid address, stored lowercase so "Dev@Example.com" and "dev@example.com" match.
ContactEmail = Annotated[
    EmailStr,
    AfterValidator(str.lower),
    Field(description="Who to contact about this app", examples=["dev@example.com"]),
]
PlanId = Annotated[
    uuid.UUID,
    Field(description="The plan that sets the consumer's rate limit and monthly quota"),
]
ConsumerName = Annotated[SlugName, Field(examples=["mobile-app"])]


class ConsumerCreate(BaseModel):
    name: ConsumerName
    email: ContactEmail
    status: ConsumerStatus = ConsumerStatus.ACTIVE
    plan_id: PlanId | None = None  # none: the workspace's default plan


class ConsumerUpdate(PartialUpdate):
    """Also how a consumer is blocked: PATCH {"status": "blocked"}."""

    name: ConsumerName | None = None
    email: ContactEmail | None = None
    status: ConsumerStatus | None = None
    plan_id: PlanId | None = None  # applies with the next config update


class ConsumerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    status: ConsumerStatus
    plan_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
