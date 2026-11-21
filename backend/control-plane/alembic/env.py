"""Alembic runs this file for every command (upgrade, downgrade, revision).

The async trick: Alembic itself is synchronous, but our driver (asyncpg)
is async. So we open an *async* connection, then hand Alembic a sync view
of it with `connection.run_sync(...)`.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.core.db import Base

# isort: split
# Import every feature's models here so Base.metadata knows their tables
# and `alembic revision --autogenerate` can see them.
from app.audit import models as audit_models  # noqa: F401
from app.consumers import models as consumer_models  # noqa: F401
from app.gateway_config import models as gateway_config_models  # noqa: F401
from app.keys import models as key_models  # noqa: F401
from app.members import models as member_models  # noqa: F401
from app.routes import models as route_models  # noqa: F401
from app.services import models as service_models  # noqa: F401
from app.users import models as user_models  # noqa: F401
from app.workspaces import models as workspace_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False: by default fileConfig mutes every logger that
    # already exists, which silently swallowed our app warnings when tests run
    # migrations in the same process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
# The owner, not the API's login: migrations create tables and grant privileges.
DATABASE_URL = get_settings().migration_database_url


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # NullPool: open one connection, use it, close it. No pool needed for a
    # one-off command.
    engine = create_async_engine(DATABASE_URL, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
