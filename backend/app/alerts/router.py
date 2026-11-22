"""Browser API for alerts. Every route requires an authorised session, the same gate as the read API.

Alerts are read and changed only through these routes; the threshold evaluator runs inside ingestion and the periodic sweep.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal

import math
from datetime import UTC

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app import errors
from app.alerts import service as S
from app.clerk_auth import Principal
from app.errors import ErrorDetail
from app.models import Alert
from app.monitoring.router import HOST_ID, _aware, _csv
from app.monitoring.sources import SOURCES
from app.registry.service import default_workspace_id

# Host-id patterns that define a source (see app/monitoring/sources.py and app/auth.py). Used in SQL so the database filters.
SYNTHETIC_ID = "^sim[0-9]*-"
REPLAY_ID = "^replay-"
MAX_HISTOGRAM_WINDOW_DAYS = 31
MAX_HISTOGRAM_BUCKETS = 2000


class AlertOut(BaseModel):
    id: uuid.UUID
    host_id: str
    condition_key: str
    rule: str
    metric: str | None
    labels: dict
    severity: str
    state: str
    value: float | None
    threshold: float | None
    opened_at: datetime
    last_evaluated_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    resolved_at: datetime | None
    resolved_by: str | None
    resolution: str | None


class AlertEventOut(BaseModel):
    event: str
    occurred_at: datetime
    actor: str
    detail: dict


class AlertDetailOut(AlertOut):
    events: list[AlertEventOut]


class AlertListOut(BaseModel):
    alerts: list[AlertOut]
    total: int


class HistogramBucketOut(BaseModel):
    start: datetime
    critical: int
    warning: int


class HistogramOut(BaseModel):
    """Alerts opened in each UTC-aligned bucket of the requested window. Every bucket is present, empty ones with zero counts.
    `complete` is always true here: the database counts every qualifying alert; no records are loaded into the browser."""
    start: datetime
    end: datetime
    bucket_seconds: int
    sources: list[str]
    buckets: list[HistogramBucketOut]
    total: int
    critical: int
    warning: int
    complete: bool


def _out(a: Alert) -> AlertOut:
    return AlertOut(id=a.id, host_id=a.host_id, condition_key=a.condition_key, rule=a.rule, metric=a.metric, labels=a.labels,
                    severity=a.severity, state=a.state, value=a.value, threshold=a.threshold, opened_at=a.opened_at,
                    last_evaluated_at=a.last_evaluated_at, acknowledged_at=a.acknowledged_at, acknowledged_by=a.acknowledged_by,
                    resolved_at=a.resolved_at, resolved_by=a.resolved_by, resolution=a.resolution)


def build_alert_router(session_factory: sessionmaker[Session], clock: Callable[[], datetime], authorize) -> APIRouter:
    router = APIRouter(prefix="/api/v1/alerts", dependencies=[Depends(authorize)])

    @router.get("", response_model=AlertListOut)
    def list_alerts(
        status: Literal["active", "resolved", "all"] = Query("active", description="active = open or acknowledged"),
        host_id: str | None = Query(None, pattern=HOST_ID),
        limit: int = Query(100, ge=1, le=200),
        _: Principal = Depends(authorize),
    ):
        with session_factory() as session:
            wid = default_workspace_id(session)
            stmt = select(Alert).where(Alert.workspace_id == wid)
            if status == "active":
                stmt = stmt.where(Alert.state.in_(S.LIVE))
            elif status == "resolved":
                stmt = stmt.where(Alert.state == "resolved")
            if host_id:
                stmt = stmt.where(Alert.host_id == host_id)
            total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
            rows = session.execute(stmt.order_by(Alert.opened_at.desc(), Alert.id).limit(limit)).scalars().all()
            return AlertListOut(alerts=[_out(a) for a in rows], total=total)

    @router.get("/histogram", response_model=HistogramOut)
    def alert_histogram(
        start: datetime = Query(..., description="Window start, with a UTC offset."),
        end: datetime = Query(..., description="Window end (exclusive), with a UTC offset."),
        bucket_seconds: int = Query(300, ge=60, le=86400),
        source: list[str] | None = Query(None, description="live, synthetic, replay; default all."),
        _: Principal = Depends(authorize),
    ):
        start_u, end_u = _aware(start, "start"), _aware(end, "end")
        if end_u <= start_u:
            raise errors.invalid_request([ErrorDetail("end", "end must be after start.")])
        if end_u - start_u > timedelta(days=MAX_HISTOGRAM_WINDOW_DAYS):
            raise errors.invalid_request([ErrorDetail("end", f"The window may be at most {MAX_HISTOGRAM_WINDOW_DAYS} days.")])
        # Buckets start on UTC epoch multiples of bucket_seconds, so every client and every refresh draws the same boundaries.
        step = bucket_seconds
        first = int(start_u.timestamp()) // step * step
        count = math.ceil((end_u.timestamp() - first) / step)
        if count > MAX_HISTOGRAM_BUCKETS:
            raise errors.invalid_request([ErrorDetail("bucket_seconds", f"That gives {count} buckets; the limit is {MAX_HISTOGRAM_BUCKETS}.")])
        sources = _csv(source, SOURCES, "source")
        selected = sorted(sources) if sources else list(SOURCES)
        with session_factory() as session:
            wid = default_workspace_id(session)
            index = func.floor(func.extract("epoch", Alert.opened_at) / step).label("idx")
            stmt = (
                select(index, Alert.severity, func.count().label("n"))
                .where(Alert.workspace_id == wid, Alert.opened_at >= start_u, Alert.opened_at < end_u)
                .group_by(index, Alert.severity)
            )
            if sources and set(sources) != set(SOURCES):
                synthetic = Alert.host_id.op("~")(SYNTHETIC_ID)
                replay = Alert.host_id.op("~")(REPLAY_ID)
                clauses = []
                if "synthetic" in sources:
                    clauses.append(synthetic)
                if "replay" in sources:
                    clauses.append(replay)
                if "live" in sources:
                    clauses.append(and_(not_(synthetic), not_(replay)))
                stmt = stmt.where(or_(*clauses))
            rows = session.execute(stmt).all()
        counts: dict[int, dict[str, int]] = {}
        for idx, severity, n in rows:
            counts.setdefault(int(idx), {"critical": 0, "warning": 0})[("critical" if severity == "critical" else "warning")] += int(n)
        buckets = []
        for i in range(count):
            key = first // step + i
            c = counts.get(key, {"critical": 0, "warning": 0})
            buckets.append(HistogramBucketOut(start=datetime.fromtimestamp(key * step, tz=UTC), critical=c["critical"], warning=c["warning"]))
        crit = sum(b.critical for b in buckets)
        warn = sum(b.warning for b in buckets)
        return HistogramOut(start=start_u, end=end_u, bucket_seconds=step, sources=selected, buckets=buckets,
                            total=crit + warn, critical=crit, warning=warn, complete=True)

    @router.get("/{alert_id}", response_model=AlertDetailOut)
    def get_alert(alert_id: uuid.UUID = Path(), _: Principal = Depends(authorize)):
        with session_factory() as session:
            alert = S._alert(session, alert_id)
            events = [AlertEventOut(event=e.event, occurred_at=e.occurred_at, actor=e.actor, detail=e.detail) for e in S.events_for(session, alert_id)]
            return AlertDetailOut(**_out(alert).model_dump(), events=events)

    @router.post("/{alert_id}/acknowledge", response_model=AlertOut)
    def acknowledge(alert_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            return _out(S.acknowledge(session, alert_id, who.user_id, clock()))

    @router.post("/{alert_id}/resolve", response_model=AlertOut)
    def resolve(alert_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            return _out(S.resolve_manually(session, alert_id, who.user_id, clock()))

    return router
