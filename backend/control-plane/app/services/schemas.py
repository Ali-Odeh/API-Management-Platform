"""Request and response shapes for /api/v1/workspaces/{slug}/services and their targets."""

import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

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

from app.core.config import get_settings
from app.core.egress import internal_host_reason
from app.core.schemas import PartialUpdate, SlugName
from app.services.models import (
    DEFAULT_HC_HEALTHY_THRESHOLD,
    DEFAULT_HC_INTERVAL_S,
    DEFAULT_HC_PATH,
    DEFAULT_HC_UNHEALTHY_THRESHOLD,
    DEFAULT_PASSIVE_EJECT_S,
    DEFAULT_PASSIVE_FAILURES,
    DEFAULT_TARGET_WEIGHT,
    DEFAULT_TIMEOUT_MS,
    LB_ROUND_ROBIN,
)

# A service has at most this many targets: each one is health-checked by
# every gateway instance, so the number is bounded like other quotas.
MAX_TARGETS_PER_SERVICE = 50

_http_url: TypeAdapter[AnyHttpUrl] = TypeAdapter(AnyHttpUrl)


def _normalize_upstream_url(value: str) -> str:
    """Accept only http(s) URLs, and drop the trailing '/'.

    'http://mock-backend:9000/' -> 'http://mock-backend:9000', so the gateway
    can safely do url + request_path without getting '//'.
    """
    url = _http_url.validate_python(value)  # rejects ftp://, "abc", ...
    if url.query or url.fragment:
        raise ValueError("the URL must not contain a query string or fragment")
    # A password here would be stored, returned by the API and sent to the
    # gateway, which ignores it anyway: refuse it rather than leak it.
    if url.username or url.password:
        raise ValueError("the URL must not contain a username or password")
    # SSRF (decision 3): a clear answer now for what is plainly internal. The
    # gateway checks the address it really connects to anyway.
    reason = internal_host_reason(url.host or "", get_settings().allowed_upstreams)
    if reason:
        raise ValueError(
            f"the URL must not point inside the platform ({reason}); "
            "only the Platform Admin can allow one (UPSTREAM_ALLOW)"
        )
    return str(url).rstrip("/")


# Reusable field types: the rules live in one place for every schema.
UpstreamUrl = Annotated[
    str,
    StringConstraints(max_length=2048),
    AfterValidator(_normalize_upstream_url),
    Field(examples=["http://mock-backend:9000"]),
]
TimeoutMs = Annotated[int, Field(ge=100, le=60_000, description="100 ms to 60 s")]
Weight = Annotated[
    int, Field(ge=1, le=1000, description="Share of the requests, relative to the other targets")
]
LbAlgorithm = Literal["round_robin"]
HcPath = Annotated[
    str,
    StringConstraints(max_length=255, pattern=r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$"),
    Field(
        description="Path the gateway calls on each target: 2xx or 3xx is healthy",
        examples=["/health"],
    ),
]
HcIntervalS = Annotated[int, Field(ge=1, le=300, description="Seconds between active checks")]
HcThreshold = Annotated[int, Field(ge=1, le=10)]
PassiveFailures = Annotated[
    int, Field(ge=1, le=100, description="Failed requests in a row that set a target aside")
]
PassiveEjectS = Annotated[int, Field(ge=1, le=3600, description="Seconds a target is set aside")]


# ---- Targets


class TargetCreate(BaseModel):
    url: UpstreamUrl
    weight: Weight = DEFAULT_TARGET_WEIGHT
    is_active: bool = True


class TargetUpdate(PartialUpdate):
    weight: Weight | None = None
    is_active: bool | None = None


class TargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    weight: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ---- Services


class _HealthSettings(BaseModel):
    lb_algorithm: LbAlgorithm = LB_ROUND_ROBIN
    hc_path: HcPath = DEFAULT_HC_PATH
    hc_interval_s: HcIntervalS = DEFAULT_HC_INTERVAL_S
    hc_unhealthy_threshold: HcThreshold = DEFAULT_HC_UNHEALTHY_THRESHOLD
    hc_healthy_threshold: HcThreshold = DEFAULT_HC_HEALTHY_THRESHOLD
    passive_failures: PassiveFailures = DEFAULT_PASSIVE_FAILURES
    passive_eject_s: PassiveEjectS = DEFAULT_PASSIVE_EJECT_S


class ServiceCreate(_HealthSettings):
    name: SlugName
    targets: list[TargetCreate] = Field(min_length=1, max_length=MAX_TARGETS_PER_SERVICE)
    timeout_ms: TimeoutMs = DEFAULT_TIMEOUT_MS
    is_active: bool = True

    @model_validator(mode="after")
    def _distinct_target_urls(self) -> Self:
        urls = [t.url for t in self.targets]
        if len(set(urls)) != len(urls):
            raise ValueError("each target URL may appear only once")
        return self


class ServiceUpdate(PartialUpdate):
    """Targets change through their own endpoints."""

    name: SlugName | None = None
    timeout_ms: TimeoutMs | None = None
    is_active: bool | None = None
    lb_algorithm: LbAlgorithm | None = None
    hc_path: HcPath | None = None
    hc_interval_s: HcIntervalS | None = None
    hc_unhealthy_threshold: HcThreshold | None = None
    hc_healthy_threshold: HcThreshold | None = None
    passive_failures: PassiveFailures | None = None
    passive_eject_s: PassiveEjectS | None = None


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    targets: list[TargetOut]
    timeout_ms: int
    is_active: bool
    lb_algorithm: str
    hc_path: str
    hc_interval_s: int
    hc_unhealthy_threshold: int
    hc_healthy_threshold: int
    passive_failures: int
    passive_eject_s: int
    created_at: datetime
    updated_at: datetime
