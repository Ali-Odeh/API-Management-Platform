"""Database connection: async engine, session factory, and the model base.

- engine:       owns the connection pool to Postgres (one per app).
- SessionLocal: makes a new AsyncSession (one unit of work) on demand.
- Base:         every table model (admins, services, ...) inherits from it,
                which is how Alembic finds them.
- get_db:       FastAPI dependency. Each request gets its own session,
                and it is closed when the request ends.
"""

from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

# Predictable constraint names (e.g. "uq_services_name") so Alembic
# migrations can find and change constraints later.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)

# expire_on_commit=False: objects stay readable after commit, which we need
# when we return them from an endpoint after saving.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
