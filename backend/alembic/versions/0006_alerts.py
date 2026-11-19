"""persistent alerts and their timeline

Revision ID: 0006
Revises: 0005

Design notes (see docs/ALERTS.md):
  * `alerts` holds one row per open or acknowledged condition. Deduplication is a DATABASE guarantee: a partial unique index
    allows at most one non-resolved row per (workspace, host, condition_key). A resolved row never blocks a new breach.
  * `alert_events` is the append-only timeline (opened, severity_changed, acknowledged, recovered, resolved, resolved_manually).
  * Same access model as 0003/0005: RLS on, no grants for the public Data-API roles, narrow column grants for the app role.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

PUBLIC_API_ROLES = "('anon', 'authenticated', 'service_role')"
TABLES = ("alerts", "alert_events")


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("host_pk", pg.UUID(as_uuid=True), sa.ForeignKey("hosts.id"), nullable=False),
        sa.Column("host_id", sa.String(128), nullable=False),
        sa.Column("condition_key", sa.String(256), nullable=False),
        sa.Column("rule", sa.String(16), nullable=False),
        sa.Column("metric", sa.String(128), nullable=True),
        sa.Column("labels", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("value", sa.Float, nullable=True),
        sa.Column("threshold", sa.Float, nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(128), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(128), nullable=True),
        sa.Column("resolution", sa.String(16), nullable=True),
        sa.CheckConstraint("rule IN ('threshold', 'host_offline')", name="ck_alerts_rule"),
        sa.CheckConstraint("severity IN ('warning', 'critical')", name="ck_alerts_severity"),
        sa.CheckConstraint("state IN ('open', 'acknowledged', 'resolved')", name="ck_alerts_state"),
        sa.CheckConstraint("resolution IS NULL OR resolution IN ('recovered', 'manual')", name="ck_alerts_resolution"),
        sa.CheckConstraint(
            "(state = 'resolved') = (resolved_at IS NOT NULL AND resolution IS NOT NULL)", name="ck_alerts_resolved_consistent"
        ),
        sa.CheckConstraint("(state = 'open') OR acknowledged_at IS NOT NULL OR state = 'resolved'", name="ck_alerts_ack_consistent"),
    )
    # The dedup guarantee: one live (open or acknowledged) alert per host and condition.
    op.execute(
        "CREATE UNIQUE INDEX uq_alerts_live_condition ON public.alerts (workspace_id, host_pk, condition_key) "
        "WHERE state <> 'resolved'"
    )
    op.create_index("ix_alerts_workspace_state_opened", "alerts", ["workspace_id", "state", "opened_at"])

    op.create_table(
        "alert_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("alert_id", pg.UUID(as_uuid=True), sa.ForeignKey("alerts.id"), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("detail", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(
            "event IN ('opened', 'severity_changed', 'acknowledged', 'recovered', 'resolved_manually')",
            name="ck_alert_events_event",
        ),
    )
    op.create_index("ix_alert_events_alert_time", "alert_events", ["alert_id", "occurred_at"])

    # --- access model (same layers as 0003) -------------------------------------------------------
    for table in TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC")
        op.execute(f"""
            DO $$
            DECLARE r text;
            BEGIN
                FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN {PUBLIC_API_ROLES} LOOP
                    EXECUTE format('REVOKE ALL ON TABLE public.{table} FROM %I', r);
                END LOOP;
            END $$
        """)
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute("""
        DO $$
        DECLARE r text;
        BEGIN
            EXECUTE 'REVOKE ALL ON SEQUENCE public.alert_events_id_seq FROM PUBLIC';
            FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role') LOOP
                EXECUTE format('REVOKE ALL ON SEQUENCE public.alert_events_id_seq FROM %I', r);
            END LOOP;
        END $$
    """)

    # The application may create and advance alerts, and may never delete them or rewrite their identity columns.
    op.execute("GRANT SELECT, INSERT ON public.alerts TO uptimetrace_app")
    op.execute(
        "GRANT UPDATE (severity, state, value, threshold, last_evaluated_at, acknowledged_at, acknowledged_by, "
        "resolved_at, resolved_by, resolution) ON public.alerts TO uptimetrace_app"
    )
    op.execute("GRANT SELECT, INSERT ON public.alert_events TO uptimetrace_app")
    op.execute("GRANT USAGE ON SEQUENCE public.alert_events_id_seq TO uptimetrace_app")
    op.execute("CREATE POLICY alerts_app_select ON public.alerts FOR SELECT TO uptimetrace_app USING (true)")
    op.execute("CREATE POLICY alerts_app_insert ON public.alerts FOR INSERT TO uptimetrace_app WITH CHECK (true)")
    op.execute("CREATE POLICY alerts_app_update ON public.alerts FOR UPDATE TO uptimetrace_app USING (true) WITH CHECK (true)")
    op.execute("CREATE POLICY alert_events_app_select ON public.alert_events FOR SELECT TO uptimetrace_app USING (true)")
    op.execute("CREATE POLICY alert_events_app_insert ON public.alert_events FOR INSERT TO uptimetrace_app WITH CHECK (true)")


def downgrade() -> None:
    op.drop_table("alert_events")
    op.drop_table("alerts")
