from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    slug: Mapped[str] = mapped_column(String(63), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Host(Base):
    """A registered host. Telemetry is accepted only for hosts that have a live credential (registry-issued)."""

    __tablename__ = "hosts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    host_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(253), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    deactivated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentCredential(Base):
    __tablename__ = "agent_credentials"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    host_pk: Mapped[uuid.UUID] = mapped_column(ForeignKey("hosts.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class McpToken(Base):
    __tablename__ = "mcp_tokens"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class MonitoringSettings(Base):
    __tablename__ = "monitoring_settings"
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)


class Alert(Base):
    """One live or resolved alert. At most one non-resolved row per (workspace, host, condition_key): see migration 0006."""

    __tablename__ = "alerts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    host_pk: Mapped[uuid.UUID] = mapped_column(ForeignKey("hosts.id"), nullable=False)
    host_id: Mapped[str] = mapped_column(String(128), nullable=False)
    condition_key: Mapped[str] = mapped_column(String(256), nullable=False)
    rule: Mapped[str] = mapped_column(String(16), nullable=False)
    metric: Mapped[str | None] = mapped_column(String(128), nullable=True)
    labels: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_evaluated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Trigger and recovery evidence (migration 0007). Written once when the alert opens / recovers; NULL = unavailable
    # (alerts opened before this evidence was recorded). The application role has no UPDATE grant on the trigger columns.
    trigger_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    trigger_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    comparison: Mapped[str | None] = mapped_column(String(4), nullable=True)
    trigger_observed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    recovery_observed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AlertEvent(Base):
    """Append-only alert timeline (the application role has INSERT and SELECT only)."""

    __tablename__ = "alert_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id"), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class InvestigationReport(Base):
    """Persistent, immutable investigation record: one per alert (migration 0007). The application role may INSERT and SELECT only."""

    __tablename__ = "investigation_reports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id"), nullable=False, unique=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)


class AuditEvent(Base):
    """Append-only (the application role has INSERT and SELECT only)."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class TelemetryEnvelope(Base):
    """One durably accepted telemetry envelope (the unit of idempotency)."""

    __tablename__ = "telemetry_envelopes"
    __table_args__ = (
        UniqueConstraint("envelope_id", name="uq_telemetry_envelopes_envelope_id"),
        Index("ix_telemetry_envelopes_host_ts", "host_id", "envelope_timestamp"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    envelope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    host_id: Mapped[str] = mapped_column(String(128), nullable=False)
    hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    envelope_timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    service: Mapped[str | None] = mapped_column(String(63), nullable=True)
    metric_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
