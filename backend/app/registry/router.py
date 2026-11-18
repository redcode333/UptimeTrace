"""Browser API for the host registry. Every route requires an authorised session (the same gate as the read API).

Responses never contain a token hash. The plaintext token appears exactly once, in the response to its creation, with
`Cache-Control: no-store`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable, Literal

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from app.clerk_auth import Principal
from app.monitoring.router import HOST_ID
from app.registry import service as S


class RegisterHostIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host_id: str = Field(pattern=HOST_ID)
    kind: Literal["live", "synthetic", "replay"] = "live"
    display_name: str | None = Field(None, max_length=253)


class IssueCredentialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, max_length=64)
    agent_id: str | None = Field(None, max_length=128)


class HostRegistryOut(BaseModel):
    host_id: str
    kind: str
    display_name: str | None
    created_at: datetime
    created_by: str
    deactivated_at: datetime | None
    telemetry_state: Literal["awaiting_first_telemetry", "reporting", "silent", "replay"]
    last_envelope_at: datetime | None
    active_credentials: int


class CredentialOut(BaseModel):
    id: uuid.UUID
    name: str
    agent_id: str
    created_at: datetime
    created_by: str
    revoked_at: datetime | None
    revoked_by: str | None


class IssuedCredentialOut(CredentialOut):
    token: str  # shown once


def _cred(c) -> dict:
    return CredentialOut.model_validate(c, from_attributes=True).model_dump(mode="json")


def build_registry_router(session_factory: sessionmaker[Session], clock: Callable[[], datetime], authorize) -> APIRouter:
    router = APIRouter(prefix="/api/v1/registry")

    def row_out(r: S.RegistryRow) -> HostRegistryOut:
        h = r.host
        return HostRegistryOut(host_id=h.host_id, kind=h.kind, display_name=h.display_name, created_at=h.created_at,
                               created_by=h.created_by, deactivated_at=h.deactivated_at, telemetry_state=r.telemetry_state,
                               last_envelope_at=r.last_envelope_at, active_credentials=r.active_credentials)

    @router.get("/hosts", response_model=list[HostRegistryOut])
    def list_registered(_: Principal = Depends(authorize)):
        with session_factory() as session:
            return [row_out(r) for r in S.registry_view(session, clock())]

    @router.post("/hosts", response_model=HostRegistryOut, status_code=201)
    def register(body: RegisterHostIn, who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            S.register_host(session, who.user_id, body.host_id, body.kind, body.display_name)
        with session_factory() as session:
            return row_out(next(r for r in S.registry_view(session, clock()) if r.host.host_id == body.host_id))

    @router.get("/hosts/{host_id}/credentials", response_model=list[CredentialOut])
    def credentials(host_id: str = Path(pattern=HOST_ID), _: Principal = Depends(authorize)):
        with session_factory() as session:
            return [CredentialOut.model_validate(c, from_attributes=True) for c in S.list_credentials(session, host_id)]

    @router.post("/hosts/{host_id}/credentials", status_code=201)
    def issue(body: IssueCredentialIn, host_id: str = Path(pattern=HOST_ID), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            issued = S.issue_credential(session, who.user_id, host_id, body.name, body.agent_id)
            payload = {**_cred(issued.credential), "token": issued.token}
        return JSONResponse(payload, status_code=201, headers={"Cache-Control": "no-store"})

    @router.delete("/hosts/{host_id}/credentials/{credential_id}", response_model=CredentialOut)
    def revoke(host_id: str = Path(pattern=HOST_ID), credential_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            cred = S.revoke_credential(session, who.user_id, host_id, credential_id, clock())
            return CredentialOut.model_validate(cred, from_attributes=True)

    return router
