"""per-workspace monitoring settings (freshness limits and thresholds)

Revision ID: 0005
Revises: 0004

One JSON document per workspace, validated by the application (app/monitoring/settings.py) and versioned for compare-and-set updates.
No row = built-in defaults. Same access model as 0003/0004; the app role may INSERT and UPDATE only the document columns.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitoring_settings",
        sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("config", pg.JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_monitoring_settings_version"),
    )
    op.execute("REVOKE ALL ON TABLE public.monitoring_settings FROM PUBLIC")
    op.execute("""
        DO $$
        DECLARE r text;
        BEGIN
            FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role') LOOP
                EXECUTE format('REVOKE ALL ON TABLE public.monitoring_settings FROM %I', r);
            END LOOP;
        END $$
    """)
    op.execute("ALTER TABLE public.monitoring_settings ENABLE ROW LEVEL SECURITY")
    op.execute("GRANT SELECT, INSERT ON public.monitoring_settings TO uptimetrace_app")
    op.execute("GRANT UPDATE (config, version, updated_at, updated_by) ON public.monitoring_settings TO uptimetrace_app")
    op.execute("CREATE POLICY monitoring_settings_app_select ON public.monitoring_settings FOR SELECT TO uptimetrace_app USING (true)")
    op.execute("CREATE POLICY monitoring_settings_app_insert ON public.monitoring_settings FOR INSERT TO uptimetrace_app WITH CHECK (true)")
    op.execute("CREATE POLICY monitoring_settings_app_update ON public.monitoring_settings FOR UPDATE TO uptimetrace_app USING (true) WITH CHECK (true)")


def downgrade() -> None:
    op.drop_table("monitoring_settings")
