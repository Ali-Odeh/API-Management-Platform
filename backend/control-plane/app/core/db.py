"""Database connection: async engine, session factory, and the model base.

- engine:       owns the connection pool to Postgres (one per app).
- SessionLocal: makes a new AsyncSession (one unit of work) on demand.
- Base:         every table model (admins, services, ...) inherits from it,
                which is how Alembic finds them.
- get_db:       FastAPI dependency. Each request gets its own session,
                and it is closed when the request ends.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

from sqlalchemy import MetaData, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings
from app.core.errors import AppError

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


def driver_dsn(database_url: str) -> str:
    """'postgresql+asyncpg://...' -> 'postgresql://...', the form asyncpg accepts directly.

    Used where we talk to asyncpg without SQLAlchemy (the LISTEN connection).
    """
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


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


async def commit_or_raise(db: AsyncSession, errors: Mapping[str, AppError]) -> None:
    """Commit; if one of the named constraints is violated, raise its AppError.

    `errors` maps a constraint name to the error the client should get, e.g.
    {"uq_services_workspace_id_name": ConflictError("A service named ...")}.
    We let the database enforce the rules instead of checking first, because
    "check, then write" can race with a parallel request. Any other violation
    is a bug, so it is re-raised as is.
    """
    await _or_raise(db, db.commit, errors)


async def flush_or_raise(db: AsyncSession, errors: Mapping[str, AppError]) -> None:
    """Like commit_or_raise, for a flush in the middle of a change (the
    transaction goes on when nothing is violated)."""
    await _or_raise(db, db.flush, errors)


async def _or_raise(
    db: AsyncSession, write: Callable[[], Awaitable[None]], errors: Mapping[str, AppError]
) -> None:
    try:
        await write()
    except IntegrityError as exc:
        await db.rollback()
        error = errors.get(violated_constraint(exc) or "")
        if error is None:
            raise
        raise error from None
