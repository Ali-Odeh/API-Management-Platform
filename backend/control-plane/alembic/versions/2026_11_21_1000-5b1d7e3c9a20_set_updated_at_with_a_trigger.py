"""set updated_at with a trigger

Revision ID: 5b1d7e3c9a20
Revises: db8a66008fec
Create Date: 2026-11-21 10:00:00

Until now only the ORM's `onupdate` set `updated_at`, so updates from triggers,
bulk statements or plain SQL left it stale. A BEFORE UPDATE trigger on every
table sets it in the database itself (PLAN.md section 5, MT.1). It moves only
when the row really changed, so writing the same values leaves it alone.

Written by hand: Alembic can't see functions or triggers. Table names are
spelled out on purpose, so this migration keeps doing the same thing even
if app code changes. Tables created later add their own trigger.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5b1d7e3c9a20"
down_revision: str | None = "db8a66008fec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "workspaces",
    "admins",
    "services",
    "routes",
    "consumers",
    "api_keys",
    "config_version",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION set_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*) THEN
                NEW.updated_at := now();
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    for table in TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_set_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at()
            """
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_set_updated_at ON {table}")
    op.execute("DROP FUNCTION set_updated_at()")
