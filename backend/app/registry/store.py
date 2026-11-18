"""Agent credential lookup against the host registry. Revocation is effective on the next request: nothing is cached.

Same interface as app.auth.CredentialStore (`authenticate`, `permits`, `__len__`) so ingestion is unchanged. A database error
propagates and becomes 503: the store fails CLOSED (an unreachable registry never accepts a token), and agents retry.
The legacy file-based credentials, if any, are still honoured so existing deployments keep working.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.auth import Credential, CredentialStore, hash_token, parse_bearer

_LOOKUP = text("""
    SELECT c.name, c.agent_id, h.host_id, h.kind
    FROM agent_credentials c JOIN hosts h ON h.id = c.host_pk
    WHERE c.token_sha256 = :digest AND c.revoked_at IS NULL AND h.deactivated_at IS NULL
""")


class RegistryCredentialStore:
    def __init__(self, session_factory: sessionmaker[Session], fallback: CredentialStore | None = None):
        self._sessions = session_factory
        self._fallback = fallback

    def __len__(self) -> int:  # informational only; the registry may legitimately start empty
        return len(self._fallback) if self._fallback else 0

    def authenticate(self, authorization_header: str | None) -> Credential | None:
        token = parse_bearer(authorization_header)
        if token is None:
            return None
        if self._fallback is not None:
            legacy = self._fallback.authenticate(authorization_header)
            if legacy is not None:
                return legacy
        digest = hash_token(token)
        with self._sessions() as session:
            row = session.execute(_LOOKUP, {"digest": digest}).first()
        if row is None:
            return None
        return Credential(row.name, digest, row.agent_id, (row.host_id,), row.kind)

    permits = staticmethod(CredentialStore.permits)
