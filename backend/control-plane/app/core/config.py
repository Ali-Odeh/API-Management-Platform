"""App settings, read once from environment variables.

pydantic-settings maps each field to an env var with the same name
(case-insensitive): `database_url` <- DATABASE_URL. If a required one is
missing, creating Settings() raises a ValidationError, so the app fails
at startup with a clear message instead of failing later on first use.

In Docker Compose the API receives only the settings it needs from .env.
"""

from functools import lru_cache
from typing import Self

from pydantic import ByteSize, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.egress import AllowList, parse_allow_list


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The API's own login (apim_app): no UPDATE or DELETE on audit events.
    database_url: str
    internal_token: str

    # GoTrue (MT.1): it logs people in; we verify its tokens and call its admin API.
    gotrue_url: str = "http://auth:9999"
    # The secret GoTrue signs its access tokens with (HS256), so we can verify them.
    gotrue_jwt_secret: str = Field(min_length=32)
    # Opens GoTrue's admin API (create, invite, ban users). A secret.
    gotrue_service_role_key: str
    # The developers' own GoTrue (`auth-portal`, P.1): open sign-up, its own
    # secret. Only its tokens open the portal API, and they open nothing else.
    portal_gotrue_jwt_secret: str = Field(min_length=32)

    @model_validator(mode="after")
    def _separate_identities(self) -> Self:
        # One secret for both would let either GoTrue's tokens be checked as
        # the other's: the audiences still differ, but the keys must too.
        if self.portal_gotrue_jwt_secret == self.gotrue_jwt_secret:
            raise ValueError("PORTAL_GOTRUE_JWT_SECRET must differ from GOTRUE_JWT_SECRET")
        return self

    # The first Platform Admin, made sure of at every startup. With a password
    # the GoTrue account is created with it; without one GoTrue sends an
    # invitation email instead.
    platform_admin_email: str
    platform_admin_password: str = ""

    # Workspace defaults (decision 6), for every workspace without its own
    # value; the Platform Admin sets one per workspace (PATCH .../limits).
    # The gateway enforces the first two (MT.8), the control plane the quotas.
    workspace_rate_limit_per_min: int = Field(default=6000, gt=0)
    workspace_max_concurrent: int = Field(default=200, gt=0)
    workspace_max_services: int = Field(default=100, gt=0)
    workspace_max_routes: int = Field(default=500, gt=0)
    workspace_max_consumers: int = Field(default=5000, gt=0)

    # Protection of every workspace (V2.2): requests per minute from one
    # client IP on a public route, and failed key attempts from one IP (in
    # the gateway's 5-minute window) before that IP is refused for a while.
    public_rate_limit_per_ip_per_min: int = Field(default=600, gt=0)
    auth_fail_limit_per_ip: int = Field(default=20, gt=0)

    # A deleted workspace can be restored this long; then the purge job
    # deletes its data (decision 10).
    purge_after_days: int = Field(default=30, ge=1)

    # Request bodies (decision 16): the limit of a route without its own, and
    # the most a route may set. Bytes, or a size such as "10MiB" (binary) or
    # "10MB" (decimal).
    max_request_body: ByteSize = Field(default=ByteSize(10 * 1024**2), gt=0)
    max_request_body_ceiling: ByteSize = Field(default=ByteSize(100 * 1024**2), gt=0)

    @model_validator(mode="after")
    def _default_within_ceiling(self) -> Self:
        if self.max_request_body > self.max_request_body_ceiling:
            raise ValueError("MAX_REQUEST_BODY must not be larger than MAX_REQUEST_BODY_CEILING")
        return self

    # Internal hosts and networks tenants' services may point at anyway
    # (decision 3, SSRF): names, addresses, networks, comma-separated. The
    # gateway reads the same value; empty allows nothing internal.
    upstream_allow: str = ""

    @field_validator("upstream_allow")
    @classmethod
    def _readable_allow_list(cls, value: str) -> str:
        parse_allow_list(value)  # a bad entry stops the start, naming it
        return value

    @property
    def allowed_upstreams(self) -> AllowList:
        return parse_allow_list(self.upstream_allow)


@lru_cache
def get_settings() -> Settings:
    """Build Settings once and reuse it (lru_cache = a simple singleton)."""
    return Settings()
