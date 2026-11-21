"""The database logins the platform uses, and making sure they exist (MT.3).

Three logins, least privilege first:
- the owner (POSTGRES_USER, MIGRATION_DATABASE_URL): owns the tables; runs
  migrations and this setup. The API never uses it for requests.
- `apim_app` (DATABASE_URL): the API's login. Reads and writes the tables,
  but may only INSERT and SELECT audit events, so a bug or an injection in
  the API can't rewrite or erase the audit log.
- `apim_retention` (AUDIT_RETENTION_DATABASE_URL): the retention job's
  login; it may only read and delete audit events.

The table privileges are granted by migrations (they are part of the schema).
Roles and passwords live at the server level, outside one database, so this
setup makes them from the URLs in .env before migrations run, on every start.
"""

import asyncpg
from sqlalchemy import make_url

from app.core.db import driver_dsn

APP_ROLE = "apim_app"
RETENTION_ROLE = "apim_retention"


async def prepare_roles(*, owner_url: str, app_url: str, retention_url: str) -> None:
    """Create or update both logins (password from their URL), and let them connect.

    Safe to repeat: an existing role gets its password set again, so it
    always matches .env. Run as the owner (a superuser or CREATEROLE).
    """
    logins = ((make_url(app_url), APP_ROLE), (make_url(retention_url), RETENTION_ROLE))
    for url, role in logins:
        if url.username != role:
            raise ValueError(f"The login in that URL must be '{role}', not '{url.username}'")

    conn = await asyncpg.connect(driver_dsn(owner_url))
    try:
        for url, role in logins:
            exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
            verb = "ALTER" if exists else "CREATE"
            # format() quotes the name (%I) and the password (%L) safely: DDL takes no $1.
            statement = await conn.fetchval(
                f"SELECT format('{verb} ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "PASSWORD %L', $1::text, $2::text)",
                role,
                url.password or "",
            )
            await conn.execute(statement)
            # auth-db-init revokes CONNECT from PUBLIC, so grant it by name.
            await conn.execute(
                await conn.fetchval(
                    "SELECT format('GRANT CONNECT ON DATABASE %I TO %I', $1::text, $2::text)",
                    url.database,
                    role,
                )
            )
    finally:
        await conn.close()
