"""workspaces, host registry, database-backed agent credentials, audit events

Revision ID: 0003
Revises: 0002

Design notes (see docs/DECISIONS.md D-004..D-006):
  * Every product record carries workspace_id from day one so M6 (real multi-tenancy) needs no redesign. Today exactly one
    workspace ('default') exists and every allowlisted user acts inside it. This is NOT tenant isolation yet.
  * Credentials are stored as SHA-256 hashes of high-entropy random tokens; the token is shown once at issue time.
    Revocation is a row update that takes effect on the very next ingest request (no API restart).
  * hosts.host_id is globally unique for now because telemetry_envelopes has no workspace column and the v1 contract is
    frozen. Moving to (workspace_id, host_id) uniqueness is an M6 decision.
  * audit_events is append-only for the application role (INSERT + SELECT only).
  * Same three-layer access model as 0002: no grants for Data-API roles, RLS on with app-role-only policies, narrow grants.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

PUBLIC_API_ROLES = "('anon', 'authenticated', 'service_role')"
TABLES = ("workspaces", "hosts", "agent_credentials", "audit_events")


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_workspaces_slug"),
    )
    op.execute("INSERT INTO workspaces (slug, name) VALUES ('default', 'Default workspace')")

    op.create_table(
        "hosts",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("host_id", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(253), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("host_id", name="uq_hosts_host_id"),
        sa.CheckConstraint("kind IN ('live', 'synthetic', 'replay')", name="ck_hosts_kind"),
    )
    op.create_index("ix_hosts_workspace", "hosts", ["workspace_id"])

    op.create_table(
        "agent_credentials",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("host_pk", pg.UUID(as_uuid=True), sa.ForeignKey("hosts.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(128), nullable=False),
        sa.Column("token_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(128), nullable=True),
        sa.UniqueConstraint("name", name="uq_agent_credentials_name"),
        sa.UniqueConstraint("token_sha256", name="uq_agent_credentials_token_sha256"),
        sa.CheckConstraint("token_sha256 ~ '^[0-9a-f]{64}$'", name="ck_agent_credentials_hash_shape"),
    )
    op.create_index("ix_agent_credentials_host", "agent_credentials", ["host_pk"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("detail", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_audit_events_ws_time", "audit_events", ["workspace_id", "occurred_at"])

    # --- access model (same layers as 0002) -------------------------------------------------------
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
            EXECUTE 'REVOKE ALL ON SEQUENCE public.audit_events_id_seq FROM PUBLIC';
            FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role') LOOP
                EXECUTE format('REVOKE ALL ON SEQUENCE public.audit_events_id_seq FROM %I', r);
            END LOOP;
        END $$
    """)

    # Least privilege for uptimetrace_app. Column-level UPDATE: a credential can only ever gain a revocation, and a host
    # can only be (de)activated or renamed; identity columns and the token hash are immutable to the application.
    op.execute("GRANT SELECT ON public.workspaces TO uptimetrace_app")
    op.execute("GRANT SELECT, INSERT ON public.hosts TO uptimetrace_app")
    op.execute("GRANT UPDATE (display_name, deactivated_at) ON public.hosts TO uptimetrace_app")
    op.execute("GRANT SELECT, INSERT ON public.agent_credentials TO uptimetrace_app")
    op.execute("GRANT UPDATE (revoked_at, revoked_by) ON public.agent_credentials TO uptimetrace_app")
    op.execute("GRANT SELECT, INSERT ON public.audit_events TO uptimetrace_app")
    op.execute("GRANT USAGE ON SEQUENCE public.audit_events_id_seq TO uptimetrace_app")

    for table, ops in (("workspaces", ("SELECT",)), ("hosts", ("SELECT", "INSERT", "UPDATE")),
                       ("agent_credentials", ("SELECT", "INSERT", "UPDATE")), ("audit_events", ("SELECT", "INSERT"))):
        for verb in ops:
            clause = "WITH CHECK (true)" if verb == "INSERT" else "USING (true)" if verb in ("SELECT",) else "USING (true) WITH CHECK (true)"
            op.execute(f"CREATE POLICY {table}_app_{verb.lower()} ON public.{table} FOR {verb} TO uptimetrace_app {clause}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
