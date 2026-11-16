"""Request and response shapes for /api/v1/consumers."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.consumers.models import DEFAULT_RATE_LIMIT_PER_MIN, ConsumerStatus
from app.core.schemas import PartialUpdate, SlugName

MAX_RATE_LIMIT_PER_MIN = 100_000

# A valid address, stored lowercase so "Dev@Example.com" and "dev@example.com" match.
ContactEmail = Annotated[
    EmailStr,
    AfterValidator(str.lower),
    Field(description="Who to contact about this app", examples=["dev@example.com"]),
]
RateLimit = Annotated[
    int,
    Field(ge=1, le=MAX_RATE_LIMIT_PER_MIN, description="Requests per minute, all keys together"),
]
ConsumerName = Annotated[SlugName, Field(examples=["mobile-app"])]


class ConsumerCreate(BaseModel):
    name: ConsumerName
    email: ContactEmail
    status: ConsumerStatus = ConsumerStatus.ACTIVE
    rate_limit_per_min: RateLimit = DEFAULT_RATE_LIMIT_PER_MIN


class ConsumerUpdate(PartialUpdate):
    """Also how a consumer is blocked: PATCH {"status": "blocked"}."""

    name: ConsumerName | None = None
    email: ContactEmail | None = None
    status: ConsumerStatus | None = None
    rate_limit_per_min: RateLimit | None = None


class ConsumerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    status: ConsumerStatus
    rate_limit_per_min: int
    created_at: datetime
    updated_at: datetime
