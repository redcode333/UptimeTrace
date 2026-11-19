"""Host registration, credential issue/revoke and the registry view. Every mutation writes an audit event in the SAME transaction."""
from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import errors
from app.auth import IDENTIFIER, NAME, TOKEN_PREFIX, CredentialError, check_scope, hash_token
from app.errors import ErrorDetail
from app.models import AgentCredential, AuditEvent, Host, Workspace
from app.monitoring import health as H
from app.monitoring.settings import load as load_settings

DEFAULT_WORKSPACE_SLUG = "default"
MAX_ACTIVE_CREDENTIALS_PER_HOST = 5


def conflict(code: str, message: str) -> errors.ApiError:
    return errors.ApiError(409, code, message)


def _workspace_id(session: Session) -> uuid.UUID:
    wid = session.execute(select(Workspace.id).where(Workspace.slug == DEFAULT_WORKSPACE_SLUG)).scalar_one_or_none()
    if wid is None:  # migration 0003 seeds it; its absence is a deployment error, not a client error
        raise RuntimeError("default workspace missing")
    return wid


default_workspace_id = _workspace_id  # public name for other modules (settings, MCP token API)


def _audit(session: Session, workspace_id: uuid.UUID, actor: str, action: str, target_type: str, target_id: str, detail: dict | None = None) -> None:
    session.add(AuditEvent(workspace_id=workspace_id, actor=actor, action=action, target_type=target_type,
                           target_id=target_id, detail=detail or {}))


def _get_host(session: Session, host_id: str) -> Host:
    host = session.execute(select(Host).where(Host.host_id == host_id)).scalar_one_or_none()
    if host is None:
        raise errors.ApiError(404, "host_not_registered", "No registered host with that id.")
    return host


def register_host(session: Session, actor: str, host_id: str, kind: str, display_name: str | None) -> Host:
    try:
        check_scope(kind, "agent-" + "x", [host_id])  # reuse the credential rules: identifier shape and kind <-> prefix pairing
    except CredentialError as exc:
        raise errors.invalid_request([ErrorDetail("host_id", str(exc))]) from None
    wid = _workspace_id(session)
    host = Host(workspace_id=wid, host_id=host_id, kind=kind, display_name=(display_name or None), created_by=actor)
    session.add(host)
    try:
        session.flush()
    except IntegrityError:
        raise conflict("host_exists", "A host with that id is already registered.") from None
    _audit(session, wid, actor, "host.registered", "host", host_id, {"kind": kind})
    return host


@dataclass(frozen=True)
class IssuedCredential:
    credential: AgentCredential
    token: str  # shown exactly once; only its hash is stored


def issue_credential(session: Session, actor: str, host_id: str, name: str | None, agent_id: str | None) -> IssuedCredential:
    host = _get_host(session, host_id)
    if host.deactivated_at is not None:
        raise conflict("host_deactivated", "The host is deactivated; it cannot receive new credentials.")
    agent_id = agent_id or ("agent-" + host_id)[:128]
    name = name or f"cred-{secrets.token_hex(6)}"
    problems = []
    if not NAME.match(name):
        problems.append(ErrorDetail("name", "1-64 characters: letters, digits, '.', '_', '-'; must start alphanumeric."))
    if not IDENTIFIER.match(agent_id):
        problems.append(ErrorDetail("agent_id", "Invalid identifier."))
    if problems:
        raise errors.invalid_request(problems)
    active = session.execute(text("SELECT count(*) FROM agent_credentials WHERE host_pk = :h AND revoked_at IS NULL"),
                             {"h": host.id}).scalar_one()
    if active >= MAX_ACTIVE_CREDENTIALS_PER_HOST:
        raise conflict("credential_limit", f"At most {MAX_ACTIVE_CREDENTIALS_PER_HOST} active credentials per host; revoke one first.")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    cred = AgentCredential(workspace_id=host.workspace_id, host_pk=host.id, name=name, agent_id=agent_id,
                           token_sha256=hash_token(token), created_by=actor)
    session.add(cred)
    try:
        session.flush()
    except IntegrityError:
        raise conflict("credential_name_taken", "A credential with that name already exists.") from None
    _audit(session, host.workspace_id, actor, "credential.issued", "credential", str(cred.id), {"host_id": host_id, "name": name})
    return IssuedCredential(cred, token)


def revoke_credential(session: Session, actor: str, host_id: str, credential_id: uuid.UUID, now: datetime) -> AgentCredential:
    host = _get_host(session, host_id)
    cred = session.execute(select(AgentCredential).where(AgentCredential.id == credential_id, AgentCredential.host_pk == host.id)
                           ).scalar_one_or_none()
    if cred is None:
        raise errors.ApiError(404, "credential_not_found", "No such credential for that host.")
    if cred.revoked_at is None:  # idempotent: revoking twice keeps the first revocation time and actor
        session.execute(update(AgentCredential).where(AgentCredential.id == cred.id).values(revoked_at=now, revoked_by=actor))
        session.refresh(cred)
        _audit(session, host.workspace_id, actor, "credential.revoked", "credential", str(cred.id), {"host_id": host_id, "name": cred.name})
    return cred


def list_credentials(session: Session, host_id: str) -> list[AgentCredential]:
    host = _get_host(session, host_id)
    return list(session.execute(select(AgentCredential).where(AgentCredential.host_pk == host.id)
                                .order_by(AgentCredential.created_at, AgentCredential.name)).scalars())


@dataclass(frozen=True)
class RegistryRow:
    host: Host
    last_envelope_at: datetime | None
    active_credentials: int
    telemetry_state: str  # awaiting_first_telemetry | reporting | silent | replay


_LAST = text("""
    SELECT h.id, (SELECT max(e.envelope_timestamp) FROM telemetry_envelopes e WHERE e.host_id = h.host_id) AS last_at,
           (SELECT count(*) FROM agent_credentials c WHERE c.host_pk = h.id AND c.revoked_at IS NULL) AS active
    FROM hosts h
""")


def registry_view(session: Session, now: datetime) -> list[RegistryRow]:
    cfg = load_settings(session, _workspace_id(session)).config
    hosts = list(session.execute(select(Host).order_by(Host.host_id)).scalars())
    extra = {r.id: r for r in session.execute(_LAST)}
    out = []
    for h in hosts:
        last_at, active = extra[h.id].last_at, extra[h.id].active
        if last_at is None:
            state = "awaiting_first_telemetry"
        else:  # same freshness rules as host health: reporting | stale | silent (offline) | replay
            state = H.reporting_status("replay" if h.kind == "replay" else h.kind, last_at, now, cfg)
        out.append(RegistryRow(h, last_at, active, state))
    return out
