"""Verification of Clerk session tokens for the browser-facing (read) API.

This is the AUTHENTICATION boundary plus a single-workspace allowlist. It is NOT multi-tenant authorization: there is no
workspace model yet, so every allowed user sees all telemetry. Real workspace membership and per-workspace data isolation
are required before production (docs/auth/clerk-auth.md).

What is verified (Clerk manual JWT verification guide):
  * algorithm: RS256 only, listed explicitly (blocks alg=none and HS256-with-the-public-key forgeries);
  * signature: against the instance's public JWKS (`{issuer}/.well-known/jwks.json`), cached, refreshed on an unknown kid;
  * exp, nbf and iat with a small leeway; iss must equal the configured issuer exactly; sub is required;
  * azp (authorized party) must be one of the configured browser origins. A token WITHOUT azp is rejected (stricter than
    Clerk's minimum: browser session tokens always carry it).
No Clerk secret key is needed or used: verification uses only public keys. The token travels in `Authorization: Bearer`, never a
cookie, so CSRF does not apply.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientConnectionError, PyJWKClientError

log = logging.getLogger("uptimetrace.auth")

MAX_TOKEN_CHARS = 8192
LEEWAY_SECONDS = 5
ALGORITHMS = ["RS256"]


class TokenInvalid(Exception):
    """Missing, malformed, expired, forged or otherwise untrustworthy token. Maps to 401. The reason is for logs only."""


class KeysUnavailable(Exception):
    """The public keys could not be fetched, so no token can be verified right now. Maps to 503."""


@dataclass(frozen=True)
class Principal:
    user_id: str
    session_id: str | None


@dataclass(frozen=True)
class ClerkConfig:
    issuer: str                                  # exactly the token's `iss`, e.g. https://example-12.clerk.accounts.dev
    authorized_parties: tuple[str, ...]          # browser origins allowed in `azp` (and by CORS)
    allowed_user_ids: frozenset[str]             # Clerk user ids allowed to read telemetry (single-workspace gate)

    @classmethod
    def from_values(cls, issuer: str | None, parties: str | None, allowed: str | None) -> "ClerkConfig":
        """Fail closed: anything missing is an error that names the VARIABLE (never a value)."""
        def split(value: str | None) -> list[str]:
            return [p.strip() for p in (value or "").split(",") if p.strip()]

        missing = []
        iss = (issuer or "").strip().rstrip("/")
        if not iss.startswith("https://") and not iss.startswith("http://localhost"):
            missing.append("CLERK_ISSUER (an https:// URL)")
        origins = tuple(o.rstrip("/") for o in split(parties))
        if not origins or any(o == "*" for o in origins):
            missing.append("CLERK_AUTHORIZED_PARTIES (explicit origins, no wildcard)")
        users = frozenset(split(allowed))
        if not users:
            missing.append("UPTIMETRACE_ALLOWED_USER_IDS (at least one Clerk user id)")
        if missing:
            raise ValueError("Clerk authentication is not configured: " + "; ".join(missing))
        return cls(iss, origins, users)

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"


class KeyProvider(Protocol):
    def get_signing_key_from_jwt(self, token: str): ...


class ClerkVerifier:
    def __init__(self, config: ClerkConfig, keys: KeyProvider | None = None):
        self.config = config
        # PyJWKClient caches keys and re-fetches when it meets an unknown kid. Short timeout: this runs per request.
        self._keys = keys or PyJWKClient(config.jwks_url, cache_keys=True, lifespan=3600, timeout=5)

    def verify(self, token: str) -> Principal:
        if not token or len(token) > MAX_TOKEN_CHARS:
            raise TokenInvalid("empty or oversized token")
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError:
            raise TokenInvalid("malformed header") from None
        if header.get("alg") not in ALGORITHMS or not header.get("kid"):
            raise TokenInvalid("unexpected algorithm or missing kid")
        try:
            key = self._keys.get_signing_key_from_jwt(token).key
        except PyJWKClientConnectionError:
            log.warning("clerk jwks unavailable")
            raise KeysUnavailable() from None
        except (PyJWKClientError, InvalidTokenError):
            raise TokenInvalid("unknown signing key") from None
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                issuer=self.config.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "sub"], "verify_aud": False},
            )
        except InvalidTokenError as exc:
            raise TokenInvalid(type(exc).__name__) from None
        azp = claims.get("azp")
        if not isinstance(azp, str) or azp.rstrip("/") not in self.config.authorized_parties:
            raise TokenInvalid("azp missing or not an authorized party")
        sub = claims["sub"]
        if not isinstance(sub, str) or not sub:
            raise TokenInvalid("bad sub")
        sid = claims.get("sid")
        return Principal(sub, sid if isinstance(sid, str) else None)

    def is_allowed(self, principal: Principal) -> bool:
        return principal.user_id in self.config.allowed_user_ids
