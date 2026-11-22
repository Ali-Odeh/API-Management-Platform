"""A usage event as the gateway writes it to the stream (backend/gateway/internal/usage).

The stream is an edge of the control plane, so every entry is validated
here before anything reaches Postgres: a malformed entry is dropped (and
counted), never stored half-right.
"""

import uuid
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from app.usage.models import PATH_MAX_LENGTH, REQUEST_ID_MAX_LENGTH

SCHEMA_VERSION = "1"


def _empty_is_none(value: object) -> object:
    return None if value == "" else value


OptionalId = Annotated[uuid.UUID | None, BeforeValidator(_empty_is_none)]


class UsageEvent(BaseModel):
    """One stream entry's fields; a missing field is the gateway's empty value."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    v: str
    id: uuid.UUID
    ts: datetime
    workspace_id: uuid.UUID
    request_id: Annotated[str | None, Field(max_length=REQUEST_ID_MAX_LENGTH)] = None
    method: Annotated[str, Field(min_length=1, max_length=10)]
    path: Annotated[str, Field(max_length=PATH_MAX_LENGTH)]
    route_id: OptionalId = None
    target_id: OptionalId = None
    consumer_id: OptionalId = None
    key_id: OptionalId = None
    status: Annotated[int, Field(ge=100, le=599)]
    latency_ms: Annotated[float, Field(ge=0)]
    upstream_ms: Annotated[float, Field(ge=0)] = 0
    bytes: Annotated[int, Field(ge=0)] = 0
    client_ip: Annotated[IPv4Address | IPv6Address | None, BeforeValidator(_empty_is_none)] = None
    ratelimit: Annotated[str | None, Field(max_length=20)] = None

    @field_validator("v")
    @classmethod
    def _known_version(cls, value: str) -> str:
        if value != SCHEMA_VERSION:
            raise ValueError(f"unknown usage event version {value!r}")
        return value

    @field_validator("ts", mode="before")
    @classmethod
    def _unix_micros(cls, value: object) -> object:
        """The gateway writes Unix microseconds."""
        if isinstance(value, str) and value.isdigit():
            return datetime.fromtimestamp(int(value) / 1_000_000, tz=UTC)
        return value
