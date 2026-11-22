"""Failure rehearsals: who may be targeted, starting and stopping, and the in-process submit path used by the executor.

Authorisation boundary: a rehearsal can target only a host registered with kind `synthetic` (host id `sim-…`), that is not
deactivated, in the default workspace. Live, replay and deactivated hosts are refused here, and the start check runs again on the
server for every request. The browser cannot choose a scenario plan, an envelope count or a metric.

Execution runs in a daemon thread of the API process. One RUNNING rehearsal per workspace is enforced by a partial unique index.
Process restarts are reconciled at startup: a RUNNING rehearsal with no progress for STALE_HEARTBEAT_SECONDS is judged INCONCLUSIVE
and its fault is ended with a recovery envelope where possible. This assumes a single API process; see the report.
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app import errors
from app.alerts.service import evaluate_after_ingest
from app.ingest.fingerprint import content_hash
from app.ingest.semantic import validate_semantics
from app.ingest.service import store_envelope
from app.ingest.storage_limits import find_nul_characters
from app.models import FailureRehearsal, FailureRehearsalStep, Host, TelemetryEnvelope
from app.monitoring.settings import load as load_settings
from app.registry import service as RS
from app.rehearsal import catalogue as C
from app.rehearsal import runner as R

log = logging.getLogger("uptimetrace.rehearsal")
_ACTIVE: dict[uuid.UUID, threading.Event] = {}
_ACTIVE_LOCK = threading.Lock()


@dataclass(frozen=True)
class Target:
    host_id: str
    kind: str
    display_name: str | None
    telemetry_state: str
    eligible: bool
    reason: str | None


def targets(session: Session, now: datetime) -> list[Target]:
    out: list[Target] = []
    for r in RS.registry_view(session, now):
        h = r.host
        if h.deactivated_at is not None:
            eligible, reason = False, "This host is deactivated, so it cannot be targeted."
        elif h.kind != "synthetic":
            eligible, reason = False, (f"Only registered synthetic hosts can be targeted. This host is '{h.kind}'. "
                                       "Rehearsals never touch live or replayed hosts.")
        else:
            eligible, reason = True, None
        out.append(Target(h.host_id, h.kind, h.display_name, r.telemetry_state, eligible, reason))
    return out


def _running(session: Session, workspace_id: uuid.UUID) -> FailureRehearsal | None:
    return session.execute(select(FailureRehearsal).where(FailureRehearsal.workspace_id == workspace_id,
                                                          FailureRehearsal.execution == "RUNNING")).scalar_one_or_none()


def get(session: Session, rehearsal_id: uuid.UUID) -> FailureRehearsal:
    wid = RS.default_workspace_id(session)
    row = session.execute(select(FailureRehearsal).where(FailureRehearsal.id == rehearsal_id,
                                                         FailureRehearsal.workspace_id == wid)).scalar_one_or_none()
    if row is None:
        raise errors.ApiError(404, "rehearsal_not_found", "No rehearsal with that id.")
    return row


def recent(session: Session, limit: int, alert_id: uuid.UUID | None = None) -> list[FailureRehearsal]:
    """Newest first. `alert_id` returns only the rehearsal(s) whose verdict cites that alert (used by the investigation page)."""
    wid = RS.default_workspace_id(session)
    stmt = select(FailureRehearsal).where(FailureRehearsal.workspace_id == wid)
    if alert_id is not None:
        stmt = stmt.where(FailureRehearsal.alert_id == alert_id)
    return list(session.execute(stmt.order_by(FailureRehearsal.created_at.desc(), FailureRehearsal.id).limit(limit)).scalars())


def steps(session: Session, rehearsal_id: uuid.UUID) -> list[FailureRehearsalStep]:
    return list(session.execute(select(FailureRehearsalStep).where(FailureRehearsalStep.rehearsal_id == rehearsal_id)
                                .order_by(FailureRehearsalStep.seq)).scalars())


def start(session_factory: sessionmaker[Session], actor: str, host_id: str, scenario_id: str, now: datetime) -> uuid.UUID:
    """Authorise and record a new rehearsal (RUNNING). The caller launches the executor after this commits."""
    scenario = C.get(scenario_id)
    if scenario is None:
        raise errors.ApiError(422, "unknown_scenario", "That scenario is not in the catalogue.")
    with session_factory() as session, session.begin():
        wid = RS.default_workspace_id(session)
        host = session.execute(select(Host).where(Host.host_id == host_id, Host.workspace_id == wid)).scalar_one_or_none()
        if host is None:
            raise errors.ApiError(404, "host_not_registered", "No registered host with that id.")
        if host.deactivated_at is not None:
            raise errors.ApiError(409, "host_deactivated", "This host is deactivated, so it cannot be targeted.")
        if host.kind != "synthetic":
            raise errors.ApiError(409, "host_not_eligible", "Only registered synthetic hosts can be targeted by a rehearsal.")
        if _running(session, wid) is not None:
            raise errors.ApiError(409, "rehearsal_already_running", "A rehearsal is already running. Stop it or wait for it to finish.")
        cfg = load_settings(session, wid).config
        warning = cfg.rules[scenario.metric].warning
        label = C.METRIC_LABEL[scenario.metric]
        if warning > scenario.reach_floor:
            raise errors.ApiError(409, "scenario_cannot_cross_threshold", (
                f"The configured {label} warning limit is {warning:.0f} %, above the {scenario.reach_floor:.0f} % this scenario reaches, "
                "so the fault could not be observed. Lower the warning limit in Alert Policies first."))
        rid = uuid.uuid4()
        session.add(FailureRehearsal(
            id=rid, workspace_id=wid, host_pk=host.id, host_id=host_id, scenario=scenario.id, execution="RUNNING",
            created_by=actor, created_at=now, started_at=now, last_progress_at=now, planned_envelopes=scenario.planned_envelopes,
            cleanup_status="not_needed",
            config={**_describe_config(scenario), "metric": scenario.metric, "warning_limit": warning,
                    "critical_limit": cfg.rules[scenario.metric].critical}))
        try:
            session.flush()
        except IntegrityError:  # the partial unique index: a concurrent start won the race
            raise errors.ApiError(409, "rehearsal_already_running", "A rehearsal is already running. Stop it or wait for it to finish.") from None
    return rid


def _describe_config(scenario: C.Scenario) -> dict:
    return {"scenario": {"id": scenario.id, "interval_seconds": scenario.interval_seconds,
                         "phases": [{"name": p.name, "ticks": p.ticks} for p in scenario.phases]},
            "limits": {"max_envelopes": C.MAX_ENVELOPES, "consecutive_failure_limit": C.CONSECUTIVE_FAILURE_LIMIT,
                       "detection_budget_seconds": C.DETECTION_BUDGET_SECONDS}}


def request_stop(session_factory: sessionmaker[Session], rehearsal_id: uuid.UUID, actor: str, now: datetime) -> FailureRehearsal:
    """Record a stop request. Idempotent: a finished rehearsal is returned unchanged. The running executor sees the flag."""
    with session_factory() as session, session.begin():
        row = get(session, rehearsal_id)
        if row.execution == "RUNNING" and row.stop_requested_at is None:
            row.stop_requested_at, row.stop_requested_by = now, actor
    with _ACTIVE_LOCK:
        event = _ACTIVE.get(rehearsal_id)
    if event is not None:
        event.set()
    with session_factory() as session:
        return get(session, rehearsal_id)


def make_submit(session_factory: sessionmaker[Session], validator, clock: Callable[[], datetime]) -> Callable[[dict], dict]:
    """Submit one envelope exactly as agent telemetry is accepted: contract validation, semantic checks, durable store, then a
    read-back of the stored payload (the executor judges only what the database returns). Alerting runs after the commit, as it
    does for agents. Any failure raises; the executor records it."""

    def submit(document: dict) -> dict:
        if validator.validate(document):
            raise R.Rejected("contract")
        if validate_semantics(document, clock()) or find_nul_characters(document):
            raise R.Rejected("semantics")
        store_envelope(session_factory, document, content_hash(document))
        with session_factory() as session:
            payload = session.execute(select(TelemetryEnvelope.payload).where(
                TelemetryEnvelope.envelope_id == uuid.UUID(document["envelope_id"]))).scalar_one_or_none()
        if payload is None:
            raise RuntimeError("envelope not found after commit")
        evaluate_after_ingest(session_factory, document["host_id"], clock())
        return payload

    return submit


def _context(session_factory, rehearsal_id: uuid.UUID, submit, clock, event: threading.Event | None) -> R.Context:
    with session_factory() as session:
        row = get(session, rehearsal_id)
        scenario = C.get(row.scenario)
        assert scenario is not None  # rows are written only for catalogue scenarios

    def stop_requested() -> bool:
        if event is not None and event.is_set():
            return True
        with session_factory() as s:
            return s.execute(select(FailureRehearsal.stop_requested_at).where(FailureRehearsal.id == rehearsal_id)).scalar_one() is not None

    def wait(seconds: float) -> bool:
        return event.wait(seconds) if event is not None else False

    return R.Context(rehearsal_id=rehearsal_id, workspace_id=row.workspace_id, host_pk=row.host_pk, host_id=row.host_id,
                     scenario=scenario, session_factory=session_factory, submit=submit, clock=clock, wait=wait,
                     stop_requested=stop_requested, started_at=row.started_at, warning_limit=float(row.config["warning_limit"]))


def launch(session_factory: sessionmaker[Session], rehearsal_id: uuid.UUID, submit, clock) -> None:
    """Start the executor in a daemon thread. Called after the RUNNING row is committed."""
    event = threading.Event()
    with _ACTIVE_LOCK:
        _ACTIVE[rehearsal_id] = event

    def run() -> None:
        try:
            ctx = _context(session_factory, rehearsal_id, submit, clock, event)
            R.execute(ctx)
        except Exception as exc:  # noqa: BLE001 - recorded by the reconciler on the next start if it could not be finalised
            log.error("rehearsal thread failed: %s", type(exc).__name__)
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.pop(rehearsal_id, None)

    threading.Thread(target=run, name=f"rehearsal-{rehearsal_id}", daemon=True).start()


def reconcile_interrupted(session_factory: sessionmaker[Session], submit, clock: Callable[[], datetime]) -> int:
    """Judge RUNNING rehearsals whose executor is gone (no progress for STALE_HEARTBEAT_SECONDS and no live thread here)."""
    now = clock()
    cutoff = now - timedelta(seconds=C.STALE_HEARTBEAT_SECONDS)
    with session_factory() as session:
        stale_ids = [r.id for r in session.execute(select(FailureRehearsal).where(
            FailureRehearsal.execution == "RUNNING", FailureRehearsal.last_progress_at < cutoff)).scalars()]
    judged = 0
    for rid in stale_ids:
        with _ACTIVE_LOCK:
            if rid in _ACTIVE:
                continue
        with session_factory() as session:
            row = session.execute(select(FailureRehearsal).where(FailureRehearsal.id == rid)).scalar_one()
            counters = R.Counters(
                planned=row.planned_envelopes, sent=row.sent_envelopes, accepted=row.accepted_envelopes,
                failed=row.failed_envelopes, seq=session.execute(select(func.count()).select_from(FailureRehearsalStep)
                                                                 .where(FailureRehearsalStep.rehearsal_id == rid)).scalar_one(),
                fault_sent=row.fault_sent, fault_accepted=row.fault_accepted, recovery_accepted=row.recovery_accepted,
                fault_onset=row.fault_confirmed_at, fault_value=row.fault_observed_value, fault_ended_at=row.fault_ended_at,
                recovery_sent_at=row.recovery_sent_at, stop=row.stop_requested_at is not None, cleanup=row.cleanup_status,
                error_class=row.error_class)
            scenario = C.get(row.scenario)
            warning = float(row.config["warning_limit"])
            started = row.started_at
            host_id, host_pk, wid = row.host_id, row.host_pk, row.workspace_id
        if scenario is None:
            continue
        ctx = R.Context(rehearsal_id=rid, workspace_id=wid, host_pk=host_pk, host_id=host_id, scenario=scenario,
                        session_factory=session_factory, submit=submit, clock=clock, wait=lambda s: False,
                        stop_requested=lambda: True, started_at=started, warning_limit=warning)
        R.finish_interrupted(ctx, counters)
        judged += 1
    return judged

