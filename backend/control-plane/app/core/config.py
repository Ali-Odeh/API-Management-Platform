"""App settings, read once from environment variables.

pydantic-settings maps each field to an env var with the same name
(case-insensitive): `database_url` <- DATABASE_URL. If a required one is
missing, creating Settings() raises a ValidationError, so the app fails
at startup with a clear message instead of failing later on first use.

In Docker the variables come from the root .env via `env_file` in
docker-compose.yml.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    internal_token: str
    admin_email: str
    admin_password: str


@lru_cache
def get_settings() -> Settings:
    """Build Settings once and reuse it (lru_cache = a simple singleton)."""
    return Settings()
