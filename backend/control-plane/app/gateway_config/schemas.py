"""The answer of GET /internal/config (PLAN.md section 6, "Gateway config").

Everything the gateway needs to serve traffic without ever touching Postgres,
per workspace. Each workspace is complete, so the gateway replaces it as a
whole; only the workspaces that changed after the gateway's `since` come back.
"""

import uuid

from pydantic import BaseModel, ConfigDict

from app.consumers.models import ConsumerStatus
from app.workspaces.models import WorkspaceStatus


class TargetConfig(BaseModel):
    """A backend instance. Until service targets exist (MT.10), each service
    has exactly one: its upstream_url, with the service's id."""

    id: uuid.UUID
    url: str
    weight: int


class ServiceConfig(BaseModel):
    """An active service."""

    id: uuid.UUID
    timeout_ms: int
    targets: list[TargetConfig]


class RouteConfig(BaseModel):
    """An active route on an active service."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service_id: uuid.UUID
    path_prefix: str
    methods: list[str]
    strip_prefix: bool
    auth_required: bool


class KeyConfig(BaseModel):
    """A non-revoked key. Blocked consumers' keys are included so the gateway can answer 403."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key_hash: str
    consumer_id: uuid.UUID
    consumer_status: ConsumerStatus
    rate_limit_per_min: int


class GatewayLimits(BaseModel):
    """The workspace's limits the gateway enforces (MT.8), as they apply:
    its own values or the platform defaults. The gateway never needs the defaults."""

    rate_limit_per_min: int
    max_concurrent: int


class WorkspaceConfigOut(BaseModel):
    """One workspace, complete, as of `version`."""

    id: uuid.UUID
    slug: str
    status: WorkspaceStatus  # active or suspended (suspended: the gateway answers 403)
    version: int
    hosts: list[str]  # the host labels it is served on: its slug (custom domains later)
    limits: GatewayLimits
    services: list[ServiceConfig]
    routes: list[RouteConfig]  # sorted by path_prefix
    keys: list[KeyConfig]  # sorted by id


class ConfigAnswer(BaseModel):
    seq: int
    full: bool  # true: every workspace; the gateway replaces everything it has
    workspaces: list[WorkspaceConfigOut]  # changed after `since` (all of them when full)
    deleted: list[uuid.UUID]  # left the config (deleted or purged) after `since`
