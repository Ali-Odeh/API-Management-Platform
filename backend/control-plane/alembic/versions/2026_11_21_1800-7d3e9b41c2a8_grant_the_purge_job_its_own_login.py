"""grant the purge job its own login

Revision ID: 7d3e9b41c2a8
Revises: 244a9a2a2216
Create Date: 2026-11-21 18:00:00

MT.5 (decision 10): the workspace purge job runs as `apim_purge`. It may
delete everything of a workspace, its audit events included (which the API's
own login can't), mark the workspace purged and record that it did. Nothing
else: it never inserts config, and it can't change other columns' meaning
beyond the workspace's status (its UPDATE grant is for that column only).

The role is made with a password by scripts/prepare_database.py; here only
without login if missing, so the migration works anywhere. Never dropped on
downgrade (roles belong to the whole server). Written by hand: Alembic
doesn't track privileges.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7d3e9b41c2a8"
down_revision: str | None = "244a9a2a2216"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "apim_purge"
# A workspace's data, children first (app/workspaces/purge.py deletes them).
TENANT_TABLES = "api_keys, routes, services, consumers, workspace_members"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} NOLOGIN;
            END IF;
        END $$
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    op.execute(f"GRANT SELECT, DELETE ON {TENANT_TABLES} TO {ROLE}")
    op.execute(f"GRANT SELECT, DELETE, INSERT ON audit_events TO {ROLE}")
    op.execute(f"GRANT SELECT, UPDATE (status) ON workspaces TO {ROLE}")
    # Deleting config rows runs the config-version trigger as this login.
    op.execute(f"GRANT SELECT, UPDATE ON config_version TO {ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON {TENANT_TABLES}, audit_events, workspaces, config_version FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
