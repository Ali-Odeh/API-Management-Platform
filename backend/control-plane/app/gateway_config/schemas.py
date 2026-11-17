"""The config snapshot the gateway downloads (plan section 6, "Gateway config snapshot").

Everything the gateway needs to serve traffic without ever touching Postgres.
"""

import uuid

from pydantic import BaseModel, ConfigDict

from app.consumers.models import ConsumerStatus


class RouteConfig(BaseModel):
    """An active route on an active service, joined with what the proxy needs."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    path_prefix: str
    methods: list[str]
    strip_prefix: bool
    auth_required: bool
    upstream_url: str
    timeout_ms: int


class KeyConfig(BaseModel):
    """A non-revoked key. Blocked consumers' keys are included so the gateway can answer 403."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key_hash: str
    consumer_id: uuid.UUID
    consumer_status: ConsumerStatus
    rate_limit_per_min: int


class ConfigSnapshot(BaseModel):
    version: int
    routes: list[RouteConfig]
    keys: list[KeyConfig]
