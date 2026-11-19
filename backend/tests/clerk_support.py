"""Locally generated RS256 keys that stand in for a Clerk instance. No network, no real Clerk account."""
from __future__ import annotations

import os
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.clerk_auth import ClerkConfig, ClerkVerifier

ISSUER = "https://example-12.clerk.accounts.dev"
ORIGIN = "http://localhost:5173"
ALLOWED_USER = "user_allowed_1"
OTHER_USER = "user_stranger_2"

_KEYS: dict[str, object] = {}


def private_key(kid: str = "ins_key_1"):
    # Separate processes (the E2E API server and the test runner) must share one key: the runner passes its PEM
    # through E2E_CLERK_PRIVATE_KEY_PEM. Unset (unit tests), a fresh key is generated in-process as before.
    if kid not in _KEYS:
        pem = os.environ.get("E2E_CLERK_PRIVATE_KEY_PEM")
        if pem:
            _KEYS[kid] = serialization.load_pem_private_key(pem.encode(), password=None)
        else:
            _KEYS[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return _KEYS[kid]


class StaticKeys:
    """Stands in for PyJWKClient: kid -> public key, with the same failure modes."""

    def __init__(self, kids=("ins_key_1",), fail: Exception | None = None):
        self.kids, self.fail = set(kids), fail

    def get_signing_key_from_jwt(self, token: str):
        from jwt.exceptions import PyJWKClientError
        if self.fail:
            raise self.fail
        kid = jwt.get_unverified_header(token).get("kid")
        if kid not in self.kids:
            raise PyJWKClientError("no matching key")
        return type("K", (), {"key": private_key(kid).public_key()})()


def config(**over) -> ClerkConfig:
    base = dict(issuer=ISSUER, authorized_parties=(ORIGIN,), allowed_user_ids=frozenset({ALLOWED_USER}))
    base.update(over)
    return ClerkConfig(**base)


def verifier(keys: StaticKeys | None = None, **over) -> ClerkVerifier:
    return ClerkVerifier(config(**over), keys or StaticKeys())


def mint(*, sub: str = ALLOWED_USER, kid: str = "ins_key_1", alg: str = "RS256", exp_in: int = 60, nbf_in: int = -5,
         iss: str = ISSUER, azp: str | None = ORIGIN, key=None, **extra) -> str:
    now = int(time.time())
    claims = {"sub": sub, "iss": iss, "iat": now - 10, "nbf": now + nbf_in, "exp": now + exp_in, "sid": "sess_1", **extra}
    if azp is not None:
        claims["azp"] = azp
    return jwt.encode(claims, key or private_key(kid), algorithm=alg, headers={"kid": kid})


def bearer(**kw) -> dict:
    return {"Authorization": f"Bearer {mint(**kw)}"}
