"""Protect the config cursor and give restored databases a new generation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3da62f0af741"
down_revision: str | None = "c6a1f0d83e52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIGGER_FUNCTIONS = ("services", "routes", "consumers", "api_keys", "workspaces")
BUMP = "public.bump_workspaces(uuid[])"
TABLES = "public.config_seq, public.workspace_config"


def upgrade() -> None:
    op.add_column(
        "config_seq",
        sa.Column(
            "generation", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
    )
    # Workspace insertion initializes its config row before bumping it. Both
    # operations run as the migration owner, even for writes by a runtime login.
    for table in TRIGGER_FUNCTIONS:
        function = f"public.config_changed_{table}()"
        op.execute(f"ALTER FUNCTION {function} SECURITY DEFINER")
        op.execute(f"ALTER FUNCTION {function} SET search_path TO pg_catalog, public, pg_temp")
        op.execute(f"REVOKE EXECUTE ON FUNCTION {function} FROM PUBLIC")
    op.execute(f"ALTER FUNCTION {BUMP} SET search_path TO pg_catalog, public, pg_temp")
    op.execute(f"REVOKE EXECUTE ON FUNCTION {BUMP} FROM apim_app, apim_purge")
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON {TABLES} FROM apim_app")


def downgrade() -> None:
    op.execute(f"GRANT INSERT, UPDATE, DELETE ON {TABLES} TO apim_app")
    op.execute(f"GRANT EXECUTE ON FUNCTION {BUMP} TO apim_app, apim_purge")
    op.execute(f"ALTER FUNCTION {BUMP} SET search_path TO public")
    for table in TRIGGER_FUNCTIONS:
        function = f"public.config_changed_{table}()"
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO PUBLIC")
        op.execute(f"ALTER FUNCTION {function} RESET search_path")
        op.execute(f"ALTER FUNCTION {function} SECURITY INVOKER")
    op.drop_column("config_seq", "generation")
