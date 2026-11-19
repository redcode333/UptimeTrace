"""alert trigger and recovery evidence; immutable investigation records

Revision ID: 0007
Revises: 0006

Design notes (see docs/INVESTIGATION.md):
  * `alerts` gains nullable evidence columns. Trigger columns are written once when an alert opens and the application role
    has NO UPDATE grant on them, so the database refuses to rewrite the trigger evidence. Recovery columns are writable by the
    application (system recovery only) and are NULL for manual resolutions.
  * Existing alerts are backfilled ONLY from values their own timeline events already recorded (the `opened` and `recovered`
    event details). Sample observation times were never recorded for them, so they stay NULL: unavailable, not inferred.
  * `investigation_reports` holds one immutable record per alert. The application role may INSERT and SELECT only; there is no
    UPDATE or DELETE grant, so a stored record cannot be edited through the application.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

PUBLIC_API_ROLES = "('anon', 'authenticated', 'service_role')"


def upgrade() -> None:
    op.add_column("alerts", sa.Column("trigger_value", sa.Float, nullable=True))
    op.add_column("alerts", sa.Column("trigger_threshold", sa.Float, nullable=True))
    op.add_column("alerts", sa.Column("comparison", sa.String(4), nullable=True))
    op.add_column("alerts", sa.Column("trigger_observed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("alerts", sa.Column("recovery_value", sa.Float, nullable=True))
    op.add_column("alerts", sa.Column("recovery_observed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_alerts_comparison", "alerts", "comparison IS NULL OR comparison IN ('>=')")
    op.create_check_constraint("ck_alerts_trigger_threshold_rule", "alerts",
                               "rule = 'threshold' OR (trigger_value IS NULL AND trigger_threshold IS NULL AND comparison IS NULL)")

    # Backfill from what the existing timeline already recorded. Threshold alerts only; host_offline carries no value.
    op.execute("""
        UPDATE public.alerts a
        SET trigger_value = (e.detail->>'value')::double precision,
            trigger_threshold = (e.detail->>'threshold')::double precision,
            comparison = '>='
        FROM public.alert_events e
        WHERE e.alert_id = a.id AND e.event = 'opened' AND a.rule = 'threshold'
          AND e.detail->>'value' IS NOT NULL AND e.detail->>'threshold' IS NOT NULL
    """)
    op.execute("""
        UPDATE public.alerts a
        SET recovery_value = (e.detail->>'value')::double precision
        FROM public.alert_events e
        WHERE e.alert_id = a.id AND e.event = 'recovered' AND a.rule = 'threshold' AND e.detail->>'value' IS NOT NULL
    """)
    # Application may record recovery evidence (system recovery) and nothing else new on existing rows.
    op.execute("GRANT UPDATE (recovery_value, recovery_observed_at) ON public.alerts TO uptimetrace_app")

    op.create_table(
        "investigation_reports",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("alert_id", pg.UUID(as_uuid=True), sa.ForeignKey("alerts.id"), nullable=False, unique=True),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("body", pg.JSONB, nullable=False),
    )
    # Same access layers as 0003/0006.
    op.execute("REVOKE ALL ON TABLE public.investigation_reports FROM PUBLIC")
    op.execute(f"""
        DO $$
        DECLARE r text;
        BEGIN
            FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN {PUBLIC_API_ROLES} LOOP
                EXECUTE format('REVOKE ALL ON TABLE public.investigation_reports FROM %I', r);
            END LOOP;
        END $$
    """)
    op.execute("ALTER TABLE public.investigation_reports ENABLE ROW LEVEL SECURITY")
    op.execute("GRANT SELECT, INSERT ON public.investigation_reports TO uptimetrace_app")
    op.execute("CREATE POLICY investigation_reports_app_select ON public.investigation_reports FOR SELECT TO uptimetrace_app USING (true)")
    op.execute("CREATE POLICY investigation_reports_app_insert ON public.investigation_reports FOR INSERT TO uptimetrace_app WITH CHECK (true)")


def downgrade() -> None:
    op.drop_table("investigation_reports")
    op.drop_constraint("ck_alerts_trigger_threshold_rule", "alerts", type_="check")
    op.drop_constraint("ck_alerts_comparison", "alerts", type_="check")
    for col in ("recovery_observed_at", "recovery_value", "trigger_observed_at", "comparison", "trigger_threshold", "trigger_value"):
        op.drop_column("alerts", col)
