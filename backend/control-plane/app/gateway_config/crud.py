"""Reading the config version. (Writing it is the database triggers' job.)"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway_config.models import SINGLETON_ID, ConfigVersion


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
