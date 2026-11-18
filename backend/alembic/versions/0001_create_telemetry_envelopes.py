"""create telemetry_envelopes

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telemetry_envelopes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("envelope_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.String(128), nullable=False),
        sa.Column("host_id", sa.String(128), nullable=False),
        sa.Column("hostname", sa.String(253), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("envelope_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service", sa.String(63), nullable=True),
        sa.Column("metric_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("envelope_id", name="uq_telemetry_envelopes_envelope_id"),
    )
    op.create_index("ix_telemetry_envelopes_host_ts", "telemetry_envelopes", ["host_id", "envelope_timestamp"])
    # Supabase exposes the public schema through PostgREST. With RLS enabled and no policies the
    # anon/authenticated API roles get no access; the backend's owner role bypasses RLS.
    op.execute("ALTER TABLE telemetry_envelopes ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_telemetry_envelopes_host_ts", table_name="telemetry_envelopes")
    op.drop_table("telemetry_envelopes")
