"""grant the API its own login

Revision ID: e41a7d2b9c05
Revises: c329c6bb5c92
Create Date: 2026-11-21 12:00:00

MT.3: the API stops using the owner (a superuser, which ignores every grant)
and connects as `apim_app`, which reads and writes the tables, now and in
the future (default privileges), but never alembic_version. The audit_events
migration then narrows that table to INSERT and SELECT.

The roles are made with a password by scripts/prepare_database.py before
migrations run; here they are only made (without login) if missing, so the
migration works anywhere. Never dropped on downgrade: roles belong to the
whole server, and other databases on it (e.g. apim_test next to apim) use
them. Written by hand: Alembic doesn't track privileges.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e41a7d2b9c05"
down_revision: str | None = "c329c6bb5c92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("apim_app", "apim_retention")


def upgrade() -> None:
    for role in ROLES:
        op.execute(
            f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                    CREATE ROLE {role} NOLOGIN;
                END IF;
            END $$
            """
        )
        op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO apim_app")
    op.execute("REVOKE ALL ON alembic_version FROM apim_app")
    # Tables made by later migrations (always run as the owner) get the same.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO apim_app"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM apim_app"
    )
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM apim_app")
    for role in ROLES:
        op.execute(f"REVOKE USAGE ON SCHEMA public FROM {role}")
