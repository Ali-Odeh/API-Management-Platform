"""Request and response shapes for /api/v1/services."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
)

from app.core.schemas import PartialUpdate, SlugName
from app.services.models import DEFAULT_TIMEOUT_MS

_http_url: TypeAdapter[AnyHttpUrl] = TypeAdapter(AnyHttpUrl)


def _normalize_upstream_url(value: str) -> str:
    """Accept only http(s) URLs, and drop the trailing '/'.

    'http://mock-backend:9000/' -> 'http://mock-backend:9000', so the gateway
    can safely do upstream_url + request_path without getting '//'.
    """
    url = _http_url.validate_python(value)  # rejects ftp://, "abc", ...
    if url.query or url.fragment:
        raise ValueError("upstream_url must not contain a query string or fragment")
    # A password here would be stored, returned by the API and sent to the
    # gateway, which ignores it anyway: refuse it rather than leak it.
    if url.username or url.password:
        raise ValueError("upstream_url must not contain a username or password")
    return str(url).rstrip("/")


# Reusable field types: the rules live in one place for Create and Update.
UpstreamUrl = Annotated[
    str,
    StringConstraints(max_length=2048),
    AfterValidator(_normalize_upstream_url),
    Field(examples=["http://mock-backend:9000"]),
]
TimeoutMs = Annotated[int, Field(ge=100, le=60_000, description="100 ms to 60 s")]


class ServiceCreate(BaseModel):
    name: SlugName
    upstream_url: UpstreamUrl
    timeout_ms: TimeoutMs = DEFAULT_TIMEOUT_MS
    is_active: bool = True


class ServiceUpdate(PartialUpdate):
    name: SlugName | None = None
    upstream_url: UpstreamUrl | None = None
    timeout_ms: TimeoutMs | None = None
    is_active: bool | None = None


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    upstream_url: str
    timeout_ms: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
