"""Workspace-scoped trace evidence. Every service and every trace shown here is resolved on the server.

Identity: a service is one of the caller's workspace services (table `services`). A trace is shown only if one of its spans was
emitted by such a service, identified by `uptimetrace.service.id`. The browser never supplies a service name, workspace or
trace filter that is trusted; trace ids are validated before any backend call.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.models import Host, Service, ServiceHost
from app.traces import summary as S
from app.traces.jaeger import JaegerClient, TraceBackendError

SEARCH_LIMIT = 50
ITEM_LIMIT = 10
TRACE_ID = re.compile(r"^[0-9a-f]{32}$")


def _us(t: datetime) -> int:
    return int(t.timestamp() * 1_000_000)


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def services_for_host(session: Session, workspace_id: uuid.UUID, host_id: str) -> list[tuple[uuid.UUID, str]]:
    rows = session.execute(
        select(Service.id, Service.name)
        .join(ServiceHost, ServiceHost.service_id == Service.id)
        .join(Host, Host.id == ServiceHost.host_pk)
        .where(Host.host_id == host_id, ServiceHost.workspace_id == workspace_id, Service.workspace_id == workspace_id)
        .order_by(Service.name, Service.id)
    ).all()
    return [(r.id, r.name) for r in rows]


def workspace_service_ids(session: Session, workspace_id: uuid.UUID) -> set[str]:
    return {str(i) for i in session.execute(select(Service.id).where(Service.workspace_id == workspace_id)).scalars().all()}


def related_for_alert(session: Session, workspace_id: uuid.UUID, host_id: str, start: datetime, end: datetime,
                      client: JaegerClient | None) -> dict[str, Any]:
    """Trace evidence for an alert's window. Never raises for a trace-backend problem: the reason says what happened."""
    services = services_for_host(session, workspace_id, host_id)
    out: dict[str, Any] = {"available": False, "reason": None, "window_start": _iso(start), "window_end": _iso(end),
                           "services": [name for _, name in services], "items": [], "truncated": False}
    if client is None:
        out["reason"] = "not_configured"
        return out
    if not services:
        out["reason"] = "no_registered_services"
        return out
    found: dict[str, dict[str, Any]] = {}
    wanted = {str(sid) for sid, _ in services}
    try:
        for sid, name in services:
            # Jaeger filters on the authenticated service identity before the limit, so this service's traces are never
            # crowded out. The ownership check below stays as the last line of defence.
            for raw in client.search(name, _us(start), _us(end), SEARCH_LIMIT, tags={S.SERVICE_ID_TAG: str(sid)}):
                if S.service_ids(raw) & wanted:
                    found.setdefault(raw.get("traceID"), raw)
    except TraceBackendError:
        out["reason"] = "unavailable"
        return out
    summaries = [S.summarize(t) for t in found.values() if S.belongs_to(t, wanted)]
    summaries.sort(key=lambda s: (-s["error_count"], -s["duration_ms"], s["trace_id"]))
    out.update({"available": True, "items": summaries[:ITEM_LIMIT], "truncated": len(summaries) > ITEM_LIMIT})
    return out


def trace_detail(session: Session, workspace_id: uuid.UUID, trace_id: str, client: JaegerClient | None) -> dict[str, Any]:
    if client is None:
        raise errors.ApiError(503, "traces_not_configured", "Application tracing is not connected to this deployment.")
    if not TRACE_ID.match(trace_id or ""):
        raise errors.ApiError(422, "invalid_trace_id", "A trace id is 32 lowercase hexadecimal characters.")
    try:
        raw = client.trace(trace_id)
    except TraceBackendError:
        raise errors.ApiError(503, "traces_unavailable", "The trace backend is not reachable right now.") from None
    # A trace outside this workspace is reported exactly like a missing one, so existence is not revealed.
    if raw is None or not S.belongs_to(raw, workspace_service_ids(session, workspace_id)):
        raise errors.ApiError(404, "trace_not_found", "No trace with this id is available to this workspace.")
    return {"summary": S.summarize(raw), **S.span_list(raw)}
