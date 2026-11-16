"""Request and response shapes for API keys."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class KeyCreate(BaseModel):
    # A label for humans, so spaces and capitals are fine (unlike slug names).
    name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
        Field(examples=["mobile app, iOS"]),
    ]


class KeyOut(BaseModel):
    """A key as listed later: never the key itself, only its prefix."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    consumer_id: uuid.UUID
    name: str
    key_prefix: str = Field(examples=["ak_live_3f9a"])
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class KeyCreated(KeyOut):
    """Only the creation response carries the raw key. It is never stored or shown again."""

    key: str = Field(
        description="The full API key. Copy it now: it can't be retrieved later.",
        examples=["ak_live_3f9a8c1d5e7b2a4f6c8d0e1f3a5b7c9d"],
    )
