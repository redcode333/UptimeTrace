"""failure rehearsals: controlled, authorised fault rehearsals on registered synthetic hosts and their recorded outcomes

Revision ID: 0009
Revises: 0008

Design notes:
  * A rehearsal targets a host registered with kind `synthetic` (host ids start with `sim-`). Nothing else can be targeted; the
    eligibility rule is enforced by the application and re-checked when a rehearsal starts.
  * Execution outcome and detection outcome are separate columns with separate CHECK constraints.
  * At most one RUNNING rehearsal per workspace (partial unique index), so two injections can never overlap.
  * Steps are append-only evidence of what was sent and what the database read back. Same access layers as 0008: RLS on, no grants
    for the public Data-API roles, and the application role gets only what it needs (no DELETE on either table).
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

PUBLIC_API_ROLES = "('anon', 'authenticated', 'service_role')"
EXECUTION = ("RUNNING", "COMPLETED", "FAILED", "STOPPED", "INCONCLUSIVE")
DETECTION = ("DETECTED", "LATE_DETECTION", "UNDETECTED", "INCONCLUSIVE")


def upgrade() -> None:
    op.create_table(
        "failure_rehearsals",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("host_pk", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("host_id", sa.String(128), nullable=False),
        sa.Column("scenario", sa.String(64), nullable=False),
        sa.Column("execution", sa.String(16), nullable=False),
        sa.Column("execution_reason", sa.Text, nullable=True),
        sa.Column("detection", sa.String(16), nullable=True),
        sa.Column("detection_reason", sa.Text, nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_requested_by", sa.String(128), nullable=True),
        sa.Column("phase", sa.String(16), nullable=True),
        sa.Column("planned_envelopes", sa.Integer, nullable=False),
        sa.Column("sent_envelopes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("accepted_envelopes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_envelopes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("fault_sent", sa.Integer, nullable=False, server_default="0"),
        sa.Column("fault_accepted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("recovery_accepted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("fault_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fault_observed_value", sa.Float, nullable=True),
        sa.Column("fault_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_status", sa.String(16), nullable=False, server_default="not_needed"),
        sa.Column("alert_id", pg.UUID(as_uuid=True), sa.ForeignKey("alerts.id"), nullable=True),
        sa.Column("alert_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alert_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detection_delay_seconds", sa.Float, nullable=True),
        sa.Column("recovery_observed", sa.Boolean, nullable=True),
        sa.Column("recovery_seconds", sa.Float, nullable=True),
        sa.Column("evidence_useful", sa.Boolean, nullable=True),
        sa.Column("evidence_points", sa.Integer, nullable=True),
        sa.Column("error_class", sa.String(64), nullable=True),
        sa.Column("config", pg.JSONB, nullable=False, server_default="{}"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_failure_rehearsals_ws_id"),
        sa.CheckConstraint(f"execution IN {EXECUTION}", name="ck_failure_rehearsals_execution"),
        sa.CheckConstraint(f"detection IS NULL OR detection IN {DETECTION}", name="ck_failure_rehearsals_detection"),
        sa.CheckConstraint("cleanup_status IN ('not_needed', 'recovered', 'failed')", name="ck_failure_rehearsals_cleanup"),
        sa.CheckConstraint("planned_envelopes > 0", name="ck_failure_rehearsals_planned"),
    )
    op.execute("ALTER TABLE public.failure_rehearsals ADD CONSTRAINT fk_failure_rehearsals_host "
               "FOREIGN KEY (workspace_id, host_pk) REFERENCES public.hosts (workspace_id, id)")
    op.execute("CREATE UNIQUE INDEX uq_failure_rehearsals_one_running ON public.failure_rehearsals (workspace_id) "
               "WHERE execution = 'RUNNING'")
    op.create_index("ix_failure_rehearsals_created", "failure_rehearsals", ["workspace_id", "created_at"])

    op.create_table(
        "failure_rehearsal_steps",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("rehearsal_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("envelope_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("error_class", sa.String(64), nullable=True),
        sa.Column("observed_value", sa.Float, nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("rehearsal_id", "seq", name="uq_failure_rehearsal_steps_seq"),
        sa.CheckConstraint("outcome IN ('accepted', 'rejected', 'failed')", name="ck_failure_rehearsal_steps_outcome"),
        sa.CheckConstraint("phase IN ('baseline', 'fault', 'recovery', 'cleanup')", name="ck_failure_rehearsal_steps_phase"),
    )
    op.execute("ALTER TABLE public.failure_rehearsal_steps ADD CONSTRAINT fk_failure_rehearsal_steps_rehearsal "
               "FOREIGN KEY (rehearsal_id) REFERENCES public.failure_rehearsals (id) ON DELETE CASCADE")

    # --- access model (same layers as 0008) -----------------------------------------------------------------
    for table in ("failure_rehearsals", "failure_rehearsal_steps"):
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
    grants = {
        "failure_rehearsals": ("SELECT", "INSERT", "UPDATE"),
        "failure_rehearsal_steps": ("SELECT", "INSERT"),
    }
    for table, verbs in grants.items():
        op.execute(f"GRANT {', '.join(verbs)} ON public.{table} TO uptimetrace_app")
        for verb in verbs:
            clause = "USING (true)" if verb in ("SELECT", "UPDATE") else ""
            wc = "WITH CHECK (true)" if verb in ("INSERT", "UPDATE") else ""
            op.execute(f"CREATE POLICY {table}_app_{verb.lower()} ON public.{table} FOR {verb} TO uptimetrace_app {clause} {wc}")
    # Identity column of the steps table: the application needs its sequence to insert.
    op.execute("GRANT USAGE ON SEQUENCE public.failure_rehearsal_steps_id_seq TO uptimetrace_app")


def downgrade() -> None:
    op.drop_table("failure_rehearsal_steps")
    op.drop_table("failure_rehearsals")
