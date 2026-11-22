"""service targets and health checks

Revision ID: 5f2c8d1a7e64
Revises: 3da62f0af741
Create Date: 2026-11-22 14:00:00

MT.10 (decision 14): a service runs on one or more targets, with a weight
each, and has load-balancing and health-check settings.

- `services` gets `lb_algorithm` and the health settings (the plan's
  defaults), with CHECKs.
- `service_targets` is new: a composite FK keeps each target in its
  service's workspace, ON DELETE CASCADE removes a service's targets with it.
- Every service's `upstream_url` becomes its one target, weight 100, with
  the service's own id as the target id: the gateway already used that id
  (MT.6), so a running gateway keeps its balancer through the upgrade. Then
  the column is dropped (decision 13: no transition period).
- The config triggers: `config_changed_services()` compares the new config
  columns instead of `upstream_url` (it would fail once the column is
  gone), and `service_targets` gets its own three triggers.
- `bump_workspaces` bumps once per transaction: creating a service with its
  targets is two statements (two triggers), but one change. The sequence
  moves and NOTIFY goes out at the first bump; later statements of the same
  transaction reuse that sequence, and each workspace's version moves once.
- The purge login may delete targets; the API's login gets the new table
  through the default privileges of the "grant the API its own login"
  migration.

Written by hand (Docker was down while building, and Alembic doesn't track
triggers or move data), then checked against the models with `alembic check`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5f2c8d1a7e64"
down_revision: str | None = "3da62f0af741"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SERVICE_CHECKS = {
    "ck_services_lb_algorithm": "lb_algorithm IN ('round_robin')",
    "ck_services_hc_path": "hc_path LIKE '/%'",
    "ck_services_hc_interval_s": "hc_interval_s BETWEEN 1 AND 300",
    "ck_services_hc_unhealthy_threshold": "hc_unhealthy_threshold BETWEEN 1 AND 10",
    "ck_services_hc_healthy_threshold": "hc_healthy_threshold BETWEEN 1 AND 10",
    "ck_services_passive_failures": "passive_failures BETWEEN 1 AND 100",
    "ck_services_passive_eject_s": "passive_eject_s BETWEEN 1 AND 3600",
}

# Kept in step with app/gateway_config/models.py (CONFIG_COLUMNS).
SERVICE_CONFIG_COLUMNS = (
    "timeout_ms",
    "is_active",
    "lb_algorithm",
    "hc_path",
    "hc_interval_s",
    "hc_unhealthy_threshold",
    "hc_healthy_threshold",
    "passive_failures",
    "passive_eject_s",
)
OLD_SERVICE_CONFIG_COLUMNS = ("upstream_url", "timeout_ms", "is_active")
TARGET_CONFIG_COLUMNS = ("service_id", "url", "weight", "is_active")


def _trigger_function(table: str, columns: Sequence[str]) -> str:
    """The function a table's three config triggers call (see the migration
    "incremental config sequence": which workspaces did the statement touch?)."""
    new = ", ".join(f"n.{c}" for c in columns)
    old = ", ".join(f"o.{c}" for c in columns)
    return f"""
        CREATE OR REPLACE FUNCTION config_changed_{table}() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public, pg_temp AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                PERFORM bump_workspaces(ARRAY(SELECT DISTINCT workspace_id FROM new_rows));
            ELSIF TG_OP = 'DELETE' THEN
                PERFORM bump_workspaces(ARRAY(SELECT DISTINCT workspace_id FROM old_rows));
            ELSE
                PERFORM bump_workspaces(ARRAY(
                    SELECT DISTINCT n.workspace_id FROM new_rows n JOIN old_rows o ON o.id = n.id
                     WHERE ROW({new}) IS DISTINCT FROM ROW({old})
                ));
            END IF;
            RETURN NULL;
        END;
        $$
    """


# One transaction, one bump (MT.10). The transaction-local setting
# apim.bumped_seq remembers the sequence this transaction already took; it
# is gone at commit or rollback (and a rolled-back savepoint takes it back
# together with its bump).
BUMP_ONCE = """
    CREATE OR REPLACE FUNCTION bump_workspaces(changed uuid[]) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public, pg_temp AS $$
    DECLARE
        taken text := current_setting('apim.bumped_seq', true);
        new_seq bigint;
    BEGIN
        IF cardinality(changed) = 0 THEN
            RETURN;  -- nothing the gateway uses changed
        END IF;
        IF taken IS NULL OR taken = '' THEN
            UPDATE config_seq SET seq = seq + 1 WHERE id = 1 RETURNING seq INTO new_seq;
            PERFORM set_config('apim.bumped_seq', new_seq::text, true);
            PERFORM pg_notify('config_changed', new_seq::text);
        ELSE
            new_seq := taken::bigint;
        END IF;
        UPDATE workspace_config SET version = version + 1, seq = new_seq
         WHERE workspace_id = ANY(changed) AND seq <> new_seq;
    END;
    $$
"""
BUMP_EACH_STATEMENT = """
    CREATE OR REPLACE FUNCTION bump_workspaces(changed uuid[]) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public, pg_temp AS $$
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


def upgrade() -> None:
    op.execute(BUMP_ONCE)

    # Health and load-balancing settings, every existing service with the defaults.
    op.add_column(
        "services",
        sa.Column("lb_algorithm", sa.String(length=20), server_default="round_robin", nullable=False),
    )
    op.add_column(
        "services",
        sa.Column("hc_path", sa.String(length=255), server_default="/health", nullable=False),
    )
    for column, default in (
        ("hc_interval_s", "10"),
        ("hc_unhealthy_threshold", "3"),
        ("hc_healthy_threshold", "2"),
        ("passive_failures", "5"),
        ("passive_eject_s", "30"),
    ):
        op.add_column("services", sa.Column(column, sa.Integer(), server_default=default, nullable=False))
    for name, condition in SERVICE_CHECKS.items():
        op.create_check_constraint(op.f(name), "services", condition)

    op.create_table(
        "service_targets",
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("weight", sa.Integer(), server_default="100", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("weight BETWEEN 1 AND 1000", name=op.f("ck_service_targets_weight")),
        sa.ForeignKeyConstraint(
            ["service_id", "workspace_id"],
            ["services.id", "services.workspace_id"],
            name="fk_service_targets_service",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_service_targets_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_targets")),
        sa.UniqueConstraint("service_id", "url", name="uq_service_targets_service_id_url"),
    )
    op.create_index(op.f("ix_service_targets_service_id"), "service_targets", ["service_id"], unique=False)
    op.create_index(op.f("ix_service_targets_workspace_id"), "service_targets", ["workspace_id"], unique=False)
    op.execute(
        "CREATE TRIGGER trg_service_targets_set_updated_at BEFORE UPDATE ON service_targets "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # The services' triggers compare the new columns from now on (CREATE OR
    # REPLACE keeps the triggers and the revoked EXECUTE privilege).
    op.execute(_trigger_function("services", SERVICE_CONFIG_COLUMNS))

    # Each service's upstream_url becomes its one target, with the service's
    # id (the target id the gateway already used). Before the targets' own
    # triggers exist: the move changes nothing the gateway serves.
    op.execute(
        "INSERT INTO service_targets (id, workspace_id, service_id, url, weight, is_active, created_at) "
        "SELECT id, workspace_id, id, upstream_url, 100, true, created_at FROM services"
    )
    op.drop_column("services", "upstream_url")

    op.execute(_trigger_function("service_targets", TARGET_CONFIG_COLUMNS))
    op.execute("REVOKE EXECUTE ON FUNCTION public.config_changed_service_targets() FROM PUBLIC")
    for event, referencing in (
        ("insert", "NEW TABLE AS new_rows"),
        ("update", "OLD TABLE AS old_rows NEW TABLE AS new_rows"),
        ("delete", "OLD TABLE AS old_rows"),
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_service_targets_config_{event}
            AFTER {event.upper()} ON service_targets
            REFERENCING {referencing}
            FOR EACH STATEMENT EXECUTE FUNCTION config_changed_service_targets()
            """
        )

    # The purge job deletes a workspace's targets before its services.
    op.execute("GRANT SELECT, DELETE ON service_targets TO apim_purge")


def downgrade() -> None:
    op.execute(BUMP_EACH_STATEMENT)
    op.execute("REVOKE ALL ON service_targets FROM apim_purge")
    op.add_column("services", sa.Column("upstream_url", sa.String(length=2048), nullable=True))
    # A service keeps its first target (the oldest): the MVP knows only one.
    op.execute(
        """
        UPDATE services s SET upstream_url = t.url
          FROM (SELECT DISTINCT ON (service_id) service_id, url FROM service_targets
                ORDER BY service_id, created_at, id) t
         WHERE t.service_id = s.id
        """
    )
    # A service left without any target can't go back to one upstream_url:
    # NOT NULL then stops the downgrade, and nothing is deleted.
    op.alter_column("services", "upstream_url", nullable=False)
    op.execute(_trigger_function("services", OLD_SERVICE_CONFIG_COLUMNS))
    op.drop_table("service_targets")  # drops its triggers too
    op.execute("DROP FUNCTION config_changed_service_targets()")
    for name in SERVICE_CHECKS:
        op.drop_constraint(op.f(name), "services", type_="check")
    for column in (
        "passive_eject_s",
        "passive_failures",
        "hc_healthy_threshold",
        "hc_unhealthy_threshold",
        "hc_interval_s",
        "hc_path",
        "lb_algorithm",
    ):
        op.drop_column("services", column)
