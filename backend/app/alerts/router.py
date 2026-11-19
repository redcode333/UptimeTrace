"""Browser API for alerts. Every route requires an authorised session, the same gate as the read API.

Alerts are read and changed only through these routes; the threshold evaluator runs inside ingestion and the periodic sweep.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.alerts import service as S
from app.clerk_auth import Principal
from app.models import Alert
from app.monitoring.router import HOST_ID
from app.registry.service import default_workspace_id


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
