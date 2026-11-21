"""incremental config sequence

Revision ID: c6a1f0d83e52
Revises: 7d3e9b41c2a8
Create Date: 2026-11-22 09:00:00

MT.6 (decision 7): `config_seq` (global) and `workspace_config` (version and
seq per workspace) replace the single `config_version`, so a change in one
workspace sends only that workspace to the gateways.

Triggers, following the Postgres rules in PLAN.md section 5:
- statement-level, with transition tables, so one statement bumps once
  however many rows it touches (cascades included);
- three per config table (insert, update, delete): a trigger with
  transition tables can't list several events with OR. `workspaces` has no
  delete trigger: its rows are never deleted (workspace_config RESTRICTs it);
- updates compare the config columns of OLD and NEW themselves: a trigger
  with transition tables can't use UPDATE OF <columns>. A statement that
  changes no config column (a rename, `updated_at`, `last_used_at`) or
  moves no row bumps nothing;
- inserting a workspace also makes its workspace_config row.

`bump_workspaces` is SECURITY DEFINER: the logins whose writes fire it
(apim_app, apim_purge) need no rights on the two config tables. The
sequence starts where config_version was, so a running gateway's next
request isn't mistaken for one from the future.

Written by hand (Alembic doesn't track triggers), then the tables checked
against the models with `alembic check`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6a1f0d83e52"
down_revision: str | None = "7d3e9b41c2a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept in step with app/gateway_config/models.py (CONFIG_COLUMNS); a test
# checks every listed column against the triggers' behavior.
CONFIG_COLUMNS = {
    "services": ("upstream_url", "timeout_ms", "is_active"),
    "routes": ("service_id", "path_prefix", "methods", "strip_prefix", "auth_required", "is_active"),
    "consumers": ("status", "rate_limit_per_min"),
    "api_keys": ("consumer_id", "key_hash", "revoked_at"),
    "workspaces": ("slug", "status", "rate_limit_per_min", "max_concurrent"),
}
# The column naming the workspace: its own id for `workspaces`.
WORKSPACE_COLUMN = {table: "workspace_id" for table in CONFIG_COLUMNS} | {"workspaces": "id"}
OLD_TABLES = ("services", "routes", "consumers", "api_keys")


def _trigger_function(table: str) -> str:
    """The function a table's three triggers call: which workspaces changed?"""
    ws = WORKSPACE_COLUMN[table]
    columns = CONFIG_COLUMNS[table]
    new = ", ".join(f"n.{c}" for c in columns)
    old = ", ".join(f"o.{c}" for c in columns)
    # PL/pgSQL plans each statement the first time it runs, so a branch that
    # names a transition table this trigger doesn't have is never planned.
    inserted = (
        f"""
            INSERT INTO workspace_config (workspace_id, version, seq)
            SELECT id, 0, 0 FROM new_rows;"""
        if table == "workspaces"
        else ""
    )
    return f"""
        CREATE FUNCTION config_changed_{table}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN{inserted}
                PERFORM bump_workspaces(ARRAY(SELECT DISTINCT {ws} FROM new_rows));
            ELSIF TG_OP = 'DELETE' THEN
                PERFORM bump_workspaces(ARRAY(SELECT DISTINCT {ws} FROM old_rows));
            ELSE
                PERFORM bump_workspaces(ARRAY(
                    SELECT DISTINCT n.{ws} FROM new_rows n JOIN old_rows o ON o.id = n.id
                     WHERE ROW({new}) IS DISTINCT FROM ROW({old})
                ));
            END IF;
            RETURN NULL;
        END;
        $$
    """


def upgrade() -> None:
    op.create_table(
        "config_seq",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("seq", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_config_seq_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_config_seq")),
    )
    op.execute("INSERT INTO config_seq (id, seq) SELECT 1, version FROM config_version")
    op.create_table(
        "workspace_config",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_config_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("workspace_id", name=op.f("pk_workspace_config")),
    )
    op.create_index(op.f("ix_workspace_config_seq"), "workspace_config", ["seq"], unique=False)
    # Every existing workspace starts at version 1, as of the current sequence.
    op.execute(
        "INSERT INTO workspace_config (workspace_id, version, seq) "
        "SELECT id, 1, (SELECT seq FROM config_seq) FROM workspaces"
    )
    op.execute(
        "CREATE TRIGGER trg_config_seq_set_updated_at BEFORE UPDATE ON config_seq "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    for table in OLD_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_bump_config_version ON {table}")
    op.execute("DROP FUNCTION bump_config_version()")
    op.drop_table("config_version")

    op.execute(
        """
        CREATE FUNCTION bump_workspaces(changed uuid[]) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            new_seq bigint;
        BEGIN
            IF cardinality(changed) = 0 THEN
                RETURN;  -- nothing the gateway uses changed
            END IF;
            UPDATE config_seq SET seq = seq + 1 WHERE id = 1 RETURNING seq INTO new_seq;
            UPDATE workspace_config SET version = version + 1, seq = new_seq
             WHERE workspace_id = ANY(changed);
            PERFORM pg_notify('config_changed', new_seq::text);
        END;
        $$
        """
    )
    # Only the logins whose writes fire the triggers may call it.
    op.execute("REVOKE ALL ON FUNCTION bump_workspaces(uuid[]) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION bump_workspaces(uuid[]) TO apim_app, apim_purge")

    for table in CONFIG_COLUMNS:
        op.execute(_trigger_function(table))
        events = [("insert", "NEW TABLE AS new_rows"), ("update", "OLD TABLE AS old_rows NEW TABLE AS new_rows")]
        if table != "workspaces":
            events.append(("delete", "OLD TABLE AS old_rows"))
        for event, referencing in events:
            op.execute(
                f"""
                CREATE TRIGGER trg_{table}_config_{event}
                AFTER {event.upper()} ON {table}
                REFERENCING {referencing}
                FOR EACH STATEMENT EXECUTE FUNCTION config_changed_{table}()
                """
            )


def downgrade() -> None:
    for table in CONFIG_COLUMNS:
        for event in ("insert", "update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_config_{event} ON {table}")
        op.execute(f"DROP FUNCTION config_changed_{table}()")
    op.execute("DROP FUNCTION bump_workspaces(uuid[])")

    op.create_table(
        "config_version",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_config_version_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_config_version")),
    )
    op.execute("INSERT INTO config_version (id, version) SELECT 1, seq FROM config_seq")
    op.execute(
        "CREATE TRIGGER trg_config_version_set_updated_at BEFORE UPDATE ON config_version "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    op.execute("GRANT SELECT, UPDATE ON config_version TO apim_purge")
    op.execute(
        """
        CREATE FUNCTION bump_config_version() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            new_version bigint;
        BEGIN
            UPDATE config_version
               SET version = version + 1, updated_at = now()
             WHERE id = 1
            RETURNING version INTO new_version;

            PERFORM pg_notify('config_changed', new_version::text);
            RETURN NULL;
        END;
        $$
        """
    )
    for table in OLD_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_bump_config_version
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION bump_config_version()
            """
        )

    op.drop_index(op.f("ix_workspace_config_seq"), table_name="workspace_config")
    op.drop_table("workspace_config")
    op.execute("DROP TRIGGER trg_config_seq_set_updated_at ON config_seq")
    op.drop_table("config_seq")
