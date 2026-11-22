"""Browser API for failure rehearsals. Every route requires an authorised session, the same gate as the alert and registry routes.

A rehearsal is started only for a registered synthetic host, and only when the request echoes that host id back as
`confirm_host_id` (a deliberate, typed confirmation). The scenario is chosen from the server's catalogue.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from app import errors
from app.clerk_auth import Principal
from app.models import FailureRehearsal, FailureRehearsalStep
from app.monitoring.router import HOST_ID
from app.rehearsal import catalogue as C
from app.rehearsal import service as S


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host_id: str = Field(pattern=HOST_ID)
    scenario: Literal["cpu_saturation", "memory_saturation"]
    confirm_host_id: str = Field(pattern=HOST_ID, description="Must equal host_id: the typed confirmation that this host is an authorised test host.")


class StepOut(BaseModel):
    seq: int
    phase: str
    sent_at: datetime
    outcome: str
    error_class: str | None
    observed_value: float | None
    observed_at: datetime | None


class RehearsalOut(BaseModel):
    id: uuid.UUID
    host_id: str
    scenario: str
    metric: str | None = None
    execution: str
    execution_reason: str | None
    detection: str | None
    detection_reason: str | None
    created_by: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    last_progress_at: datetime | None
    stop_requested_at: datetime | None
    stop_requested_by: str | None
    phase: str | None
    planned_envelopes: int
    sent_envelopes: int
    accepted_envelopes: int
    failed_envelopes: int
    fault_sent: int
    fault_accepted: int
    recovery_accepted: int
    fault_confirmed_at: datetime | None
    fault_observed_value: float | None
    fault_ended_at: datetime | None
    recovery_sent_at: datetime | None
    cleanup_status: str
    alert_id: uuid.UUID | None
    alert_opened_at: datetime | None
    alert_resolved_at: datetime | None
    detection_delay_seconds: float | None
    recovery_observed: bool | None
    recovery_seconds: float | None
    evidence_useful: bool | None
    evidence_points: int | None
    error_class: str | None
    config: dict
    steps: list[StepOut] | None = None


class TargetOut(BaseModel):
    host_id: str
    kind: str
    display_name: str | None
    telemetry_state: str
    eligible: bool
    reason: str | None


class RehearsalListOut(BaseModel):
    rehearsals: list[RehearsalOut]


def _out(r: FailureRehearsal, steps: list[FailureRehearsalStep] | None = None) -> RehearsalOut:
    base = {c.name: getattr(r, c.name) for c in FailureRehearsal.__table__.columns}
    base["config"] = dict(r.config or {})
    scenario = C.get(r.scenario)
    base["metric"] = scenario.metric if scenario else None
    return RehearsalOut(**base, steps=None if steps is None else [
        StepOut(seq=s.seq, phase=s.phase, sent_at=s.sent_at, outcome=s.outcome, error_class=s.error_class,
                observed_value=s.observed_value, observed_at=s.observed_at) for s in steps])


def build_rehearsal_router(session_factory: sessionmaker[Session], clock: Callable[[], datetime], authorize, submit) -> APIRouter:
    """`submit` is the in-process ingest path (service.make_submit), shared with startup reconciliation."""
    router = APIRouter(prefix="/api/v1/rehearsals", dependencies=[Depends(authorize)])

    @router.get("/catalogue")
    def catalogue(_: Principal = Depends(authorize)):
        return {"scenarios": [C.describe(s) for s in C.SCENARIOS.values()]}

    @router.get("/targets", response_model=list[TargetOut])
    def targets(_: Principal = Depends(authorize)):
        with session_factory() as session:
            return [TargetOut(**t.__dict__) for t in S.targets(session, clock())]

    @router.get("", response_model=RehearsalListOut)
    def list_rehearsals(limit: int = Query(20, ge=1, le=100), alert_id: uuid.UUID | None = Query(None),
                        _: Principal = Depends(authorize)):
        with session_factory() as session:
            return RehearsalListOut(rehearsals=[_out(r) for r in S.recent(session, limit, alert_id)])

    @router.post("", response_model=RehearsalOut, status_code=201)
    def start(body: StartIn, who: Principal = Depends(authorize)):
        if body.confirm_host_id != body.host_id:
            raise errors.invalid_request([errors.ErrorDetail("confirm_host_id", "Must equal host_id to confirm the target.")])
        rid = S.start(session_factory, who.user_id, body.host_id, body.scenario, clock())
        S.launch(session_factory, rid, submit, clock)
        with session_factory() as session:
            return _out(S.get(session, rid), steps=[])

    @router.get("/{rehearsal_id}", response_model=RehearsalOut)
    def detail(rehearsal_id: uuid.UUID = Path(), _: Principal = Depends(authorize)):
        with session_factory() as session:
            row = S.get(session, rehearsal_id)
            return _out(row, S.steps(session, rehearsal_id))

    @router.post("/{rehearsal_id}/stop", response_model=RehearsalOut)
    def stop(rehearsal_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        row = S.request_stop(session_factory, rehearsal_id, who.user_id, clock())
        return _out(row, steps=None)

    return router
