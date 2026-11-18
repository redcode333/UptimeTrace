"""MCP access tokens: issue, list, revoke, verify.

A token is `utm_` + 256 random bits. Only its SHA-256 is stored; the plaintext is returned once. The workspace is stored with
the token and is the ONLY source of workspace identity for every MCP call. Expiry is mandatory (default 30 days, max 90).
Verification reads the database on every call (no cache) so revocation is immediate.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app import errors
from app.auth import NAME, hash_token
from app.errors import ErrorDetail
from app.models import AuditEvent, McpToken

log = logging.getLogger("uptimetrace.mcp")
TOKEN_PREFIX = "utm_"          # distinct from agent tokens (utk_): an agent token can never be an MCP token or vice versa
SCOPES = ("hosts:read", "metrics:read")
DEFAULT_TTL_DAYS, MAX_TTL_DAYS = 30, 90
MAX_ACTIVE_PER_WORKSPACE = 20


@dataclass(frozen=True)
class IssuedMcpToken:
    record: McpToken
    token: str  # shown once


def issue(session: Session, actor: str, workspace_id: uuid.UUID, name: str, scopes: list[str], ttl_days: int, now: datetime) -> IssuedMcpToken:
    problems = []
    if not NAME.match(name):
        problems.append(ErrorDetail("name", "1-64 characters: letters, digits, '.', '_', '-'; must start alphanumeric."))
    bad = sorted(set(scopes) - set(SCOPES))
    if bad or not scopes:
        problems.append(ErrorDetail("scopes", f"One or more of {list(SCOPES)} is required."))
    if not 1 <= ttl_days <= MAX_TTL_DAYS:
        problems.append(ErrorDetail("expires_in_days", f"Must be between 1 and {MAX_TTL_DAYS}."))
    if problems:
        raise errors.invalid_request(problems)
    active = session.execute(text("SELECT count(*) FROM mcp_tokens WHERE workspace_id = :w AND revoked_at IS NULL AND expires_at > :n"),
                             {"w": workspace_id, "n": now}).scalar_one()
    if active >= MAX_ACTIVE_PER_WORKSPACE:
        raise errors.ApiError(409, "token_limit", f"At most {MAX_ACTIVE_PER_WORKSPACE} active MCP tokens per workspace.")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    rec = McpToken(workspace_id=workspace_id, name=name, token_sha256=hash_token(token), scopes=sorted(set(scopes)),
                   created_by=actor, created_at=now, expires_at=now + timedelta(days=ttl_days))
    session.add(rec)
    try:
        session.flush()
    except IntegrityError:
        raise errors.ApiError(409, "token_name_taken", "An MCP token with that name already exists.") from None
    session.add(AuditEvent(workspace_id=workspace_id, actor=actor, action="mcp_token.issued", target_type="mcp_token",
                           target_id=str(rec.id), detail={"name": name, "scopes": rec.scopes, "expires_at": rec.expires_at.isoformat()}))
    return IssuedMcpToken(rec, token)


def list_tokens(session: Session, workspace_id: uuid.UUID) -> list[McpToken]:
    return list(session.execute(select(McpToken).where(McpToken.workspace_id == workspace_id)
                                .order_by(McpToken.created_at, McpToken.name)).scalars())


def revoke(session: Session, actor: str, workspace_id: uuid.UUID, token_id: uuid.UUID, now: datetime) -> McpToken:
    # The workspace filter is part of the lookup: a token id from another workspace is simply "not found".
    rec = session.execute(select(McpToken).where(McpToken.id == token_id, McpToken.workspace_id == workspace_id)).scalar_one_or_none()
    if rec is None:
        raise errors.ApiError(404, "mcp_token_not_found", "No such MCP token.")
    if rec.revoked_at is None:
        session.execute(update(McpToken).where(McpToken.id == rec.id).values(revoked_at=now, revoked_by=actor))
        session.refresh(rec)
        session.add(AuditEvent(workspace_id=workspace_id, actor=actor, action="mcp_token.revoked", target_type="mcp_token",
                               target_id=str(rec.id), detail={"name": rec.name}))
    return rec


@dataclass(frozen=True)
class VerifiedToken:
    id: uuid.UUID
    name: str
    workspace_id: uuid.UUID
    scopes: tuple[str, ...]
    expires_at: datetime


def verify(session_factory: sessionmaker[Session], token: str, now: datetime) -> VerifiedToken | None:
    """None for anything not currently valid: unknown, malformed, expired or revoked. One answer, no reason leaked."""
    if not token.startswith(TOKEN_PREFIX) or len(token) > 200:
        return None
    with session_factory() as session:
        rec = session.execute(select(McpToken).where(McpToken.token_sha256 == hash_token(token))).scalar_one_or_none()
    if rec is None or rec.revoked_at is not None or rec.expires_at <= now:
        return None
    return VerifiedToken(rec.id, rec.name, rec.workspace_id, tuple(rec.scopes), rec.expires_at)


class SdkTokenVerifier:
    """Adapter for the MCP SDK's TokenVerifier protocol; carries the workspace in the token's claims."""

    def __init__(self, session_factory: sessionmaker[Session], clock: Callable[[], datetime]):
        self._sf, self._clock = session_factory, clock

    async def verify_token(self, token: str):
        from mcp.server.auth.provider import AccessToken
        from starlette.concurrency import run_in_threadpool

        try:
            v = await run_in_threadpool(verify, self._sf, token, self._clock())
        except SQLAlchemyError as exc:   # registry unreachable: FAIL CLOSED (the token is not accepted); never log SQL/parameters
            log.error("mcp token verification failed: %s", type(exc).__name__)
            return None
        if v is None:
            return None
        return AccessToken(token="<redacted>", client_id=v.name, scopes=list(v.scopes), expires_at=int(v.expires_at.timestamp()),
                           claims={"workspace_id": str(v.workspace_id), "token_id": str(v.id), "token_name": v.name})
