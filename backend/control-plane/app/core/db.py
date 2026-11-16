"""Database connection: async engine, session factory, and the model base.

- engine:       owns the connection pool to Postgres (one per app).
- SessionLocal: makes a new AsyncSession (one unit of work) on demand.
- Base:         every table model (admins, services, ...) inherits from it,
                which is how Alembic finds them.
- get_db:       FastAPI dependency. Each request gets its own session,
                and it is closed when the request ends.
"""

from collections.abc import AsyncIterator, Mapping

from sqlalchemy import MetaData
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings
from app.core.errors import ConflictError

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


# ---- Turning database rule violations into clean errors


def violated_constraint(exc: IntegrityError) -> str | None:
    """Name of the constraint the database rejected, e.g. "uq_services_workspace_id_name".

    The asyncpg driver error (kept as the cause of SQLAlchemy's wrapper) carries
    the exact name, which is more reliable than searching the message text.
    """
    driver_error = exc.orig.__cause__ if exc.orig is not None else None
    name = getattr(driver_error, "constraint_name", None)
    return name if isinstance(name, str) else None


async def commit_or_conflict(db: AsyncSession, conflicts: Mapping[str, str]) -> None:
    """Commit; if one of the named constraints is violated, raise ConflictError (409).

    `conflicts` maps a constraint name to the message the client should see.
    We let the database enforce the rules instead of checking first, because
    "check, then write" can race with a parallel request. Any other violation
    is a bug, so it is re-raised as is.
    """
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        message = conflicts.get(violated_constraint(exc) or "")
        if message is None:
            raise
        raise ConflictError(message) from None
