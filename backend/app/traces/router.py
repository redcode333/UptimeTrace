"""Browser API for one trace. Same authorisation gate as investigations; the workspace is resolved on the server."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session, sessionmaker

from app.clerk_auth import Principal
from app.registry.service import default_workspace_id
from app.traces import service as TS
from app.traces.jaeger import JaegerClient


def build_trace_router(session_factory: sessionmaker[Session], clock: Callable[[], datetime], authorize,
                       client: JaegerClient | None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/traces", dependencies=[Depends(authorize)])

    @router.get("/{trace_id}")
    def get_trace(trace_id: str = Path(max_length=64), _: Principal = Depends(authorize)):
        with session_factory() as session:
            return TS.trace_detail(session, default_workspace_id(session), trace_id, client)

    return router
