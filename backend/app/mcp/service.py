"""Workspace-scoped read services behind the MCP tools. No SQL beyond the registry join; no new business logic.

Every function takes the workspace id that the SERVER resolved from the verified token. Host visibility is the registry:
a host is visible iff it is registered in that workspace and not deactivated. `telemetry_envelopes` carries no workspace
column (frozen v1 contract), so an unregistered host is invisible to MCP by construction (fail closed).
An id from another workspace and an id that does not exist produce the identical error (no existence oracle).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.errors import ErrorDetail
from app.models import Host
from app.monitoring import queries as Q
from app.monitoring import service as S

MAX_LIST = 100
DEFAULT_LIST = 50
MCP_MAX_RANGE = timedelta(days=7)
MCP_MAX_POINTS = 500
DEFAULT_WINDOW = timedelta(hours=1)
METRICS = tuple(S.SUMMARY_METRICS)


def _not_found(host_id: str) -> errors.ApiError:
    return errors.ApiError(404, "host_not_found", "No host with that id is accessible in this workspace.")


def visible_hosts(session: Session, workspace_id: uuid.UUID) -> dict[str, Host]:
    rows = session.execute(select(Host).where(Host.workspace_id == workspace_id, Host.deactivated_at.is_(None))).scalars()
    return {h.host_id: h for h in rows}


def console_url(base: str | None, host_id: str) -> str | None:
    """A link only to a route that exists in the console (frontend/src/app/App.tsx: `hosts/:hostId`); None when unconfigured."""
    return f"{base.rstrip('/')}/hosts/{quote(host_id, safe='')}" if base else None


def _item(reg: Host, built, base: str | None) -> dict:
    out = {"host_id": reg.host_id, "display_name": reg.display_name, "kind": reg.kind, "console_url": console_url(base, reg.host_id)}
    if built is None:
        # Registered but never reported: say so. Health is UNKNOWN, never healthy and never a zero reading.
        return {**out, "telemetry_state": "awaiting_first_telemetry", "health": {"status": "unknown", "reasons": []},
                "last_seen": None, "last_seen_age_seconds": None, "latest": None}
    return {**out, "telemetry_state": built.reporting_status, "hostname": built.hostname,
            "health": built.health.model_dump(mode="json"), "last_seen": built.last_seen.isoformat(),
            "last_seen_age_seconds": built.last_seen_age_seconds, "latest": built.latest.model_dump(mode="json")}


def list_hosts(session: Session, workspace_id: uuid.UUID, now: datetime, limit: int, offset: int, base: str | None) -> dict:
    if not 1 <= limit <= MAX_LIST or offset < 0:
        raise errors.invalid_request([ErrorDetail("limit", f"limit must be 1..{MAX_LIST} and offset >= 0.")])
    allowed = visible_hosts(session, workspace_id)
    seen = [h for h in Q.fetch_hosts(session) if h.host_id in allowed]
    latest = Q.fetch_latest(session, seen, S.SUMMARY_METRICS)
    built = {h.host_id: S.build_host(h, latest[h.host_id], now) for h in seen}
    ids = sorted(allowed)
    page = ids[offset: offset + limit]
    return {"total": len(ids), "limit": limit, "offset": offset, "as_of": now.isoformat(),
            "hosts": [_item(allowed[i], built.get(i), base) for i in page]}


def get_host_health(session: Session, workspace_id: uuid.UUID, now: datetime, host_id: str, base: str | None) -> dict:
    reg = visible_hosts(session, workspace_id).get(host_id)
    if reg is None:
        raise _not_found(host_id)
    hosts = Q.fetch_hosts(session, host_id)
    if not hosts:
        return {"as_of": now.isoformat(), **_item(reg, None, base), "metrics": []}
    built = S.build_host(hosts[0], Q.fetch_latest(session, hosts)[host_id], now, with_metrics=True)
    return {"as_of": now.isoformat(), **_item(reg, built, base), "agent_id": built.agent_id,
            "metrics": [m.model_dump(mode="json") for m in built.metrics]}


def get_host_metrics(session: Session, workspace_id: uuid.UUID, now: datetime, host_id: str, metric: str,
                     start: datetime | None, end: datetime | None, step: int | None, agg: str, base: str | None) -> dict:
    if metric not in METRICS:
        raise errors.invalid_request([ErrorDetail("metric", f"metric must be one of {list(METRICS)}.")])
    if agg not in ("avg", "min", "max", "last"):
        raise errors.invalid_request([ErrorDetail("agg", "agg must be avg, min, max or last.")])
    for label, value in (("start", start), ("end", end)):
        if value is not None and value.tzinfo is None:
            raise errors.invalid_request([ErrorDetail(label, "Timestamp must include a UTC offset, e.g. 2026-11-11T12:00:00Z.")])
    end_t = end.astimezone(UTC) if end else now
    start_t = start.astimezone(UTC) if start else end_t - DEFAULT_WINDOW
    if end_t - start_t > MCP_MAX_RANGE:
        raise errors.invalid_request([ErrorDetail("start", f"The range may not exceed {MCP_MAX_RANGE.days} days over MCP.")])
    if host_id not in visible_hosts(session, workspace_id):
        raise _not_found(host_id)
    result = S.load_series(session, host_id, metric, start_t, end_t, step, agg, max_points=MCP_MAX_POINTS)
    body = result.model_dump(mode="json")
    body["console_url"] = console_url(base, host_id)
    # Gaps are explicit nulls (no data), never zeros; say so for the consumer.
    body["note"] = "A null point means no samples arrived in that bucket; it is not a zero reading."
    return body
