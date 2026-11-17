"""Reading the config version and the config snapshot.

(Writing the version is the database triggers' job, see models.py.)
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.consumers.models import Consumer
from app.gateway_config.models import SINGLETON_ID, ConfigVersion
from app.gateway_config.schemas import ConfigSnapshot, KeyConfig, RouteConfig
from app.keys.models import ApiKey
from app.routes.models import Route
from app.services.models import Service

_VERSION_QUERY = select(ConfigVersion.version).where(ConfigVersion.id == SINGLETON_ID)


async def get_config_version(db: AsyncSession) -> ConfigVersion:
    """The current version row, always read fresh from the database.

    populate_existing: if this session already loaded the row, refresh it
    instead of returning the cached copy, since triggers change it behind
    the ORM's back.
    """
    query = (
        select(ConfigVersion)
        .where(ConfigVersion.id == SINGLETON_ID)
        .execution_options(populate_existing=True)
    )
    return (await db.scalars(query)).one()


async def read_version(engine: AsyncEngine) -> int:
    """Just the number: a cheap query, run on every gateway request."""
    async with engine.connect() as conn:
        return (await conn.execute(_VERSION_QUERY)).scalar_one()


async def read_snapshot(engine: AsyncEngine) -> ConfigSnapshot:
    """The version and everything the gateway needs, from ONE consistent view.

    REPEATABLE READ: every query in this transaction sees the database as it
    was at the first query. Without it, a change committing between reading
    the version and reading the routes could label old routes with a new
    version, and the gateway would believe it is up to date and miss that
    change for good.
    """
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="REPEATABLE READ")
        async with conn.begin():
            version = (await conn.execute(_VERSION_QUERY)).scalar_one()
            route_rows = await conn.execute(
                select(
                    Route.id,
                    Route.path_prefix,
                    Route.methods,
                    Route.strip_prefix,
                    Route.auth_required,
                    Service.upstream_url,
                    Service.timeout_ms,
                )
                .join(Service, Route.service_id == Service.id)
                .where(Route.is_active, Service.is_active)
                .order_by(Route.path_prefix)
            )
            key_rows = await conn.execute(
                select(
                    ApiKey.id,
                    ApiKey.key_hash,
                    ApiKey.consumer_id,
                    Consumer.status.label("consumer_status"),
                    Consumer.rate_limit_per_min,
                )
                .join(Consumer, ApiKey.consumer_id == Consumer.id)
                .where(ApiKey.revoked_at.is_(None))
                .order_by(ApiKey.id)
            )
            return ConfigSnapshot(
                version=version,
                routes=[RouteConfig.model_validate(row) for row in route_rows],
                keys=[KeyConfig.model_validate(row) for row in key_rows],
            )
