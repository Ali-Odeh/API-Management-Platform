"""Make sure the database logins exist before migrations run (see app/core/db_roles.py).

    python -m scripts.prepare_database

Run by the control-plane-db-init job before `alembic upgrade head`.
"""

import asyncio
import os

from app.core.db_roles import prepare_roles


def main() -> None:
    asyncio.run(
        prepare_roles(
            owner_url=os.environ["MIGRATION_DATABASE_URL"],
            app_url=os.environ["DATABASE_URL"],
            retention_url=os.environ["AUDIT_RETENTION_DATABASE_URL"],
            purge_url=os.environ["PURGE_DATABASE_URL"],
            ingest_url=os.environ["INGEST_DATABASE_URL"],
        )
    )


if __name__ == "__main__":
    main()
