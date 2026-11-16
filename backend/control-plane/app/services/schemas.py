"""Request and response shapes for /api/v1/services."""

import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from app.services.models import DEFAULT_TIMEOUT_MS

_http_url = TypeAdapter(AnyHttpUrl)


def _normalize_upstream_url(value: str) -> str:
    """Accept only http(s) URLs, and drop the trailing '/'.

    'http://mock-backend:9000/' -> 'http://mock-backend:9000', so the gateway
    can safely do upstream_url + request_path without getting '//'.
    """
    url = _http_url.validate_python(value)  # rejects ftp://, "abc", ...
    if url.query or url.fragment:
        raise ValueError("upstream_url must not contain a query string or fragment")
    return str(url).rstrip("/")


# Reusable field types: the rules live in one place for Create and Update.
ServiceName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=100, pattern=r"^[a-z0-9_-]+$"),
    Field(description="Lowercase letters, digits, '-' and '_'", examples=["orders"]),
]
UpstreamUrl = Annotated[
    str,
    StringConstraints(max_length=2048),
    AfterValidator(_normalize_upstream_url),
    Field(examples=["http://mock-backend:9000"]),
]
TimeoutMs = Annotated[int, Field(ge=100, le=60_000, description="100 ms to 60 s")]


class ServiceCreate(BaseModel):
    name: ServiceName
    upstream_url: UpstreamUrl
    timeout_ms: TimeoutMs = DEFAULT_TIMEOUT_MS
    is_active: bool = True


class ServiceUpdate(BaseModel):
    """PATCH: every field is optional. Only the fields you send are changed."""

    name: ServiceName | None = None
    upstream_url: UpstreamUrl | None = None
    timeout_ms: TimeoutMs | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        # Leaving a field out means "don't change it". Sending null would
        # mean "erase it", which no column here allows.
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    upstream_url: str
    timeout_ms: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
