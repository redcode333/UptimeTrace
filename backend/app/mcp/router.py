"""Console API to manage MCP access tokens. Same session gate as the rest of the browser API; the workspace is resolved
server-side (today: the single default workspace) and is never accepted from the request."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from app.clerk_auth import Principal
from app.mcp import tokens as T
from app.registry.service import _workspace_id


class IssueTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=64)
    scopes: list[str] = Field(default_factory=lambda: list(T.SCOPES))
    expires_in_days: int = T.DEFAULT_TTL_DAYS


class McpTokenOut(BaseModel):
    id: uuid.UUID
    name: str
    scopes: list[str]
    created_at: datetime
    created_by: str
    expires_at: datetime
    revoked_at: datetime | None
    revoked_by: str | None


def build_mcp_token_router(session_factory: sessionmaker[Session], clock: Callable[[], datetime], authorize) -> APIRouter:
    router = APIRouter(prefix="/api/v1/registry/mcp-tokens")

    @router.get("", response_model=list[McpTokenOut])
    def list_tokens(_: Principal = Depends(authorize)):
        with session_factory() as session:
            return [McpTokenOut.model_validate(t, from_attributes=True) for t in T.list_tokens(session, _workspace_id(session))]

    @router.post("", status_code=201)
    def issue(body: IssueTokenIn, who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            issued = T.issue(session, who.user_id, _workspace_id(session), body.name, body.scopes, body.expires_in_days, clock())
            payload = {**McpTokenOut.model_validate(issued.record, from_attributes=True).model_dump(mode="json"), "token": issued.token}
        return JSONResponse(payload, status_code=201, headers={"Cache-Control": "no-store"})

    @router.delete("/{token_id}", response_model=McpTokenOut)
    def revoke(token_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            rec = T.revoke(session, who.user_id, _workspace_id(session), token_id, clock())
            return McpTokenOut.model_validate(rec, from_attributes=True)

    return router
