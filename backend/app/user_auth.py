"""FastAPI dependencies for the browser API. Agent ingestion keeps its own bearer credentials (app/auth.py)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app import errors
from app.clerk_auth import ClerkVerifier, KeysUnavailable, Principal, TokenInvalid

log = logging.getLogger("uptimetrace.auth")


def make_dependencies(verifier: ClerkVerifier | None):
    """`authenticate`: a valid Clerk session (401/503 otherwise). `authorize`: authenticated AND on the allowlist (403).

    With no verifier configured every protected route answers 401: closed, never open.
    """

    def authenticate(request: Request) -> Principal:
        if verifier is None:
            raise errors.session_required()
        parts = (request.headers.get("authorization") or "").split(" ")
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise errors.session_required()
        try:
            return verifier.verify(parts[1])
        except TokenInvalid as exc:
            log.info("session token rejected: %s", exc)  # reason only, never the token
            raise errors.session_required() from None
        except KeysUnavailable:
            raise errors.auth_unavailable() from None

    def authorize(principal: Principal = Depends(authenticate)) -> Principal:
        if verifier is None or not verifier.is_allowed(principal):
            raise errors.not_authorized()
        return principal

    return authenticate, authorize


class MeOut(BaseModel):
    user_id: str
    authorized: bool


def build_me_router(verifier: ClerkVerifier | None, authenticate) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/me", response_model=MeOut)
    def me(principal: Principal = Depends(authenticate)):
        """Who am I? Needs only a valid session, so a signed-in but not-yet-allowed user can find their id."""
        return MeOut(user_id=principal.user_id, authorized=bool(verifier and verifier.is_allowed(principal)))

    return router
