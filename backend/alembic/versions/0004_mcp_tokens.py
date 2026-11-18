"""MCP access tokens (workspace-bound, scoped, expiring, revocable)

Revision ID: 0004
Revises: 0003

Tokens authenticate an external MCP client. The workspace is stored WITH the token and resolved server-side on every call;
nothing the client sends can select a workspace. Only the SHA-256 of the token is stored. Same access model as 0003:
no Data-API grants, RLS on, column-level UPDATE (revocation fields only).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_tokens",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("token_sha256", sa.String(64), nullable=False),
        sa.Column("scopes", pg.ARRAY(sa.String(32)), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(128), nullable=True),
        sa.UniqueConstraint("name", name="uq_mcp_tokens_name"),
        sa.UniqueConstraint("token_sha256", name="uq_mcp_tokens_token_sha256"),
        sa.CheckConstraint("token_sha256 ~ '^[0-9a-f]{64}$'", name="ck_mcp_tokens_hash_shape"),
        sa.CheckConstraint("cardinality(scopes) > 0", name="ck_mcp_tokens_scopes_nonempty"),
        sa.CheckConstraint("expires_at > created_at", name="ck_mcp_tokens_expiry_after_creation"),
    )
    op.create_index("ix_mcp_tokens_workspace", "mcp_tokens", ["workspace_id"])
    op.execute("REVOKE ALL ON TABLE public.mcp_tokens FROM PUBLIC")
    op.execute("""
        DO $$
        DECLARE r text;
        BEGIN
            FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role') LOOP
                EXECUTE format('REVOKE ALL ON TABLE public.mcp_tokens FROM %I', r);
            END LOOP;
        END $$
    """)
    op.execute("ALTER TABLE public.mcp_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("GRANT SELECT, INSERT ON public.mcp_tokens TO uptimetrace_app")
    op.execute("GRANT UPDATE (revoked_at, revoked_by) ON public.mcp_tokens TO uptimetrace_app")
    op.execute("CREATE POLICY mcp_tokens_app_select ON public.mcp_tokens FOR SELECT TO uptimetrace_app USING (true)")
    op.execute("CREATE POLICY mcp_tokens_app_insert ON public.mcp_tokens FOR INSERT TO uptimetrace_app WITH CHECK (true)")
    op.execute("CREATE POLICY mcp_tokens_app_update ON public.mcp_tokens FOR UPDATE TO uptimetrace_app USING (true) WITH CHECK (true)")


def downgrade() -> None:
    op.drop_table("mcp_tokens")
