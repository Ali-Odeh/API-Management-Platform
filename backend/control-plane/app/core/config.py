"""App settings, read once from environment variables.

pydantic-settings maps each field to an env var with the same name
(case-insensitive): `database_url` <- DATABASE_URL. If a required one is
missing, creating Settings() raises a ValidationError, so the app fails
at startup with a clear message instead of failing later on first use.

In Docker Compose the API receives only the settings it needs from .env.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # A deleted workspace can be restored this long; then the purge job
    # deletes its data (decision 10).
    purge_after_days: int = Field(default=30, ge=1)


@lru_cache
def get_settings() -> Settings:
    """Build Settings once and reuse it (lru_cache = a simple singleton)."""
    return Settings()
