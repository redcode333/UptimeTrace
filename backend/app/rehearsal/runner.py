"""Executes one rehearsal plan and writes its verdict. The executor never decides pass/fail itself: it records what was sent, what the
database read back, and which alert (if any) opened and resolved, then hands those facts to `evaluate.evaluate`.

Safety properties enforced here:
  * the plan is the catalogue's fixed plan; the number of envelopes is bounded by it (and by MAX_ENVELOPES, asserted at import);
  * a stop request is honoured before every envelope, and the fault is always ended with a recovery envelope when one is needed
    (`cleanup`), including after a stop, an abandoned plan, or an unexpected exception;
  * every envelope goes through the same validation and durable storage as agent telemetry (the `submit` callable is built by the
    service from the ingest validators and `store_envelope`).
"""
from __future__ import annotations

import logging
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.alerts.rules import RULE_THRESHOLD
from app.ingest.semantic import parse_timestamp
from app.investigation import service as I
from app.models import Alert, FailureRehearsal, FailureRehearsalStep
from app.monitoring.health import series_key
from app.rehearsal import catalogue as C
from app.rehearsal import evaluate as E
from app.rehearsal.samples import phase_sampler
from simulator.scenarios import State, build_envelope

log = logging.getLogger("uptimetrace.rehearsal")
AGENT_ID = "sim-rehearsal-agent"
EVIDENCE_WINDOW_MINUTES = 30
assert C.MAX_ENVELOPES >= max(s.planned_envelopes for s in C.SCENARIOS.values())


class Rejected(Exception):
    """The envelope failed the same contract or semantic checks that agent telemetry must pass."""


@dataclass
class Context:
    rehearsal_id: uuid.UUID
    workspace_id: uuid.UUID
    host_pk: uuid.UUID
    host_id: str
    scenario: C.Scenario
    session_factory: sessionmaker[Session]
    submit: Callable[[dict], dict]          # validates, stores durably, reads back and evaluates; returns the stored payload
    clock: Callable[[], datetime]
    wait: Callable[[float], bool]           # sleep between envelopes; True when a stop was requested meanwhile
    stop_requested: Callable[[], bool]      # reads the durable stop flag
    started_at: datetime
    warning_limit: float                    # configured warning limit for the scenario's metric at start (a fault is confirmed at or above it)


@dataclass
class Counters:
    planned: int
    sent: int = 0
    accepted: int = 0
    failed: int = 0
    seq: int = 0
    consecutive: int = 0
    fault_sent: int = 0
    fault_accepted: int = 0
    recovery_sent: int = 0
    recovery_accepted: int = 0
    fault_onset: datetime | None = None
    fault_value: float | None = None
    fault_ended_at: datetime | None = None
    recovery_sent_at: datetime | None = None
    stop: bool = False
    aborted: bool = False
    interrupted: bool = False
    cleanup: str = "not_needed"
    error_class: str | None = None
    phase: str | None = None


def _observed(payload: dict, metric: str) -> tuple[float | None, datetime | None]:
    """The scenario's metric sample of a stored envelope, as the database returned it. Only the unlabelled series counts."""
    for m in payload.get("metrics", []):
        if m.get("name") == metric and not m.get("labels"):
            return float(m["value"]), parse_timestamp(m["timestamp"])
    return None, None


def _envelope(ctx: Context, c: Counters, rng, id_rng, state, source: str, t: int, now: datetime) -> dict:
    return build_envelope(source, t, c.planned, rng, state, id_rng, agent_id=AGENT_ID, host_id=ctx.host_id,
                          hostname=f"{ctx.host_id}.local", service=None, now=now, interval=ctx.scenario.interval_seconds)


def _send(ctx: Context, c: Counters, phase: str, envelope: dict, sent_at: datetime) -> None:
    c.seq += 1
    c.sent += 1
    c.phase = phase
    if phase == "fault":
        c.fault_sent += 1
        c.fault_ended_at = sent_at
    if phase == "recovery":
        c.recovery_sent += 1
        c.recovery_sent_at = c.recovery_sent_at or sent_at
    envelope_id = uuid.UUID(envelope["envelope_id"])
    try:
        stored = ctx.submit(envelope)
    except Exception as exc:  # noqa: BLE001 - every failure is recorded by class name; messages may echo payload fragments
        c.failed += 1
        c.consecutive += 1
        outcome = "rejected" if isinstance(exc, Rejected) else "failed"
        _step(ctx, c, phase, envelope_id, sent_at, outcome, error_class=type(exc).__name__)
        if c.consecutive >= C.CONSECUTIVE_FAILURE_LIMIT:
            c.aborted = True
        return
    c.consecutive = 0
    c.accepted += 1
    value, observed = _observed(stored, ctx.scenario.metric)
    if phase == "fault":
        c.fault_accepted += 1
        if c.fault_onset is None and value is not None and value >= ctx.warning_limit and observed is not None:
            c.fault_onset, c.fault_value = observed, value
    if phase == "recovery":
        c.recovery_accepted += 1
    _step(ctx, c, phase, envelope_id, sent_at, "accepted", value=value, observed=observed)


def _step(ctx: Context, c: Counters, phase: str, envelope_id: uuid.UUID, sent_at: datetime, outcome: str,
          value: float | None = None, observed: datetime | None = None, error_class: str | None = None) -> None:
    """Persist one envelope's evidence and the run's progress in one transaction (a crash keeps the last committed step)."""
    with ctx.session_factory() as session, session.begin():
        session.add(FailureRehearsalStep(rehearsal_id=ctx.rehearsal_id, workspace_id=ctx.workspace_id, seq=c.seq, phase=phase,
                                         envelope_id=envelope_id, sent_at=sent_at, outcome=outcome, error_class=error_class,
                                         observed_value=value, observed_at=observed))
        session.execute(update(FailureRehearsal).where(FailureRehearsal.id == ctx.rehearsal_id).values(
            phase=phase, sent_envelopes=c.sent, accepted_envelopes=c.accepted,
            failed_envelopes=c.failed, fault_sent=c.fault_sent, fault_accepted=c.fault_accepted,
            recovery_accepted=c.recovery_accepted, fault_confirmed_at=c.fault_onset, fault_observed_value=c.fault_value,
            fault_ended_at=c.fault_ended_at, recovery_sent_at=c.recovery_sent_at, cleanup_status=c.cleanup,
            last_progress_at=ctx.clock(), error_class=c.error_class))


def _run_plan(ctx: Context, c: Counters, rng, id_rng, state) -> None:
    total = c.planned
    tick = 0
    for phase in ctx.scenario.phases:
        for j in range(phase.ticks):
            if ctx.stop_requested():
                c.stop = True
                return
            now = ctx.clock()
            source, t = phase_sampler(phase.source, j, phase.ticks, total, tick)
            _send(ctx, c, phase.name, _envelope(ctx, c, rng, id_rng, state, source, t, now), now)
            if c.aborted:
                return
            tick += 1
            if tick < total and ctx.wait(ctx.scenario.interval_seconds):
                c.stop = True
                return


def _cleanup(ctx: Context, c: Counters, rng, id_rng, state) -> None:
    """End the injected fault: one normal-baseline envelope, accepted or recorded as a failed cleanup."""
    now = ctx.clock()
    envelope = _envelope(ctx, c, rng, id_rng, state, "normal_baseline", 0, now)
    envelope_id = uuid.UUID(envelope["envelope_id"])
    c.seq += 1
    try:
        ctx.submit(envelope)
    except Exception as exc:  # noqa: BLE001
        c.cleanup = "failed"
        c.error_class = type(exc).__name__
        _step(ctx, c, "cleanup", envelope_id, now, "rejected" if isinstance(exc, Rejected) else "failed", error_class=type(exc).__name__)
        return
    c.cleanup = "recovered"
    c.recovery_accepted += 1
    c.recovery_sent_at = c.recovery_sent_at or now
    _step(ctx, c, "cleanup", envelope_id, now, "accepted")


def _alert_for_run(ctx: Context, session: Session) -> tuple[Alert | None, bool]:
    """The NEW alert for the scenario's metric opened by this run (if any), and whether one was already open when the run started."""
    key = series_key(ctx.scenario.metric, {})
    rows = session.execute(select(Alert).where(
        Alert.workspace_id == ctx.workspace_id, Alert.host_id == ctx.host_id, Alert.condition_key == key,
        Alert.rule == RULE_THRESHOLD,
        or_(Alert.resolved_at.is_(None), Alert.resolved_at >= ctx.started_at),
    ).order_by(Alert.opened_at)).scalars().all()
    preexisting = any(a.opened_at < ctx.started_at for a in rows)
    new = [a for a in rows if a.opened_at >= ctx.started_at]
    return (new[0] if new else None), preexisting


def _evidence(session: Session, ctx: Context, alert: Alert, now: datetime) -> tuple[bool, int]:
    """Useful evidence: the trigger is recorded in full and the stored telemetry around it has at least one value."""
    view = I.build_view(session, alert, now, I.Window(EVIDENCE_WINDOW_MINUTES), 1, 0)
    points = sum(1 for s in view["series"] if s["window"] == "around_trigger" for p in s["points"] if p["value"] is not None)
    useful = alert.trigger_value is not None and alert.trigger_observed_at is not None and points > 0
    return useful, points


def _finish(ctx: Context, c: Counters, rng, id_rng, state) -> None:
    # The fault must never be left on the host: end it when no recovery telemetry was accepted.
    if c.fault_sent > 0 and c.recovery_accepted == 0:
        _cleanup(ctx, c, rng, id_rng, state)
    now = ctx.clock()
    with ctx.session_factory() as session, session.begin():
        alert, preexisting = _alert_for_run(ctx, session)
        evidence_useful = evidence_points = None
        if alert is not None:
            try:
                evidence_useful, evidence_points = _evidence(session, ctx, alert, now)
            except Exception as exc:  # noqa: BLE001 - evidence is recorded as not computed, never as useful
                log.error("rehearsal evidence check failed: %s", type(exc).__name__)
        alert_facts = E.AlertFacts(
            opened_at=alert.opened_at if alert else None, resolved_at=alert.resolved_at if alert else None,
            resolution=alert.resolution if alert else None, preexisting_live=preexisting)
        run = E.RunFacts(
            planned=c.planned, sent=c.sent, accepted=c.accepted, fault_sent=c.fault_sent, fault_accepted=c.fault_accepted,
            recovery_accepted=c.recovery_accepted, fault_confirmed=c.fault_onset is not None, stop_requested=c.stop,
            aborted=c.aborted, interrupted=c.interrupted, cleanup=c.cleanup, subject=C.METRIC_LABEL[ctx.scenario.metric])
        verdict = E.evaluate(run, c.fault_onset, c.recovery_sent_at, alert_facts)
        session.execute(update(FailureRehearsal).where(FailureRehearsal.id == ctx.rehearsal_id).values(
            execution=verdict.execution, execution_reason=verdict.execution_reason,
            detection=verdict.detection, detection_reason=verdict.detection_reason,
            ended_at=now, last_progress_at=now, phase=None,
            sent_envelopes=c.sent, accepted_envelopes=c.accepted, failed_envelopes=c.failed,
            fault_sent=c.fault_sent, fault_accepted=c.fault_accepted, recovery_accepted=c.recovery_accepted,
            fault_confirmed_at=c.fault_onset, fault_observed_value=c.fault_value, fault_ended_at=c.fault_ended_at,
            recovery_sent_at=c.recovery_sent_at, cleanup_status=c.cleanup,
            alert_id=alert.id if alert else None, alert_opened_at=alert_facts.opened_at, alert_resolved_at=alert_facts.resolved_at,
            detection_delay_seconds=verdict.detection_delay_seconds, recovery_observed=verdict.recovery_observed,
            recovery_seconds=verdict.recovery_seconds, evidence_useful=evidence_useful, evidence_points=evidence_points,
            error_class=c.error_class))


def execute(ctx: Context) -> None:
    """Run the plan to its verdict. An unexpected exception is recorded as an interrupted run and still ends the fault."""
    c = Counters(planned=ctx.scenario.planned_envelopes)
    rng = random.Random(f"{ctx.rehearsal_id}|samples")
    id_rng = random.Random(f"{ctx.rehearsal_id}|ids")
    state = State(rx=rng.randint(10**9, 5 * 10**9), tx=rng.randint(10**9, 5 * 10**9))
    try:
        _run_plan(ctx, c, rng, id_rng, state)
    except Exception as exc:  # noqa: BLE001
        c.interrupted = True
        c.error_class = type(exc).__name__
        log.error("rehearsal run failed unexpectedly: %s", type(exc).__name__)
    _finish_guarded(ctx, c, rng, id_rng, state)


def finish_interrupted(ctx: Context, c: Counters) -> None:
    """Judge a run whose executor went away (server restart). Its counters come from the persisted row."""
    c.interrupted = True
    c.error_class = c.error_class or "interrupted"
    rng = random.Random(f"{ctx.rehearsal_id}|cleanup")
    id_rng = random.Random(f"{ctx.rehearsal_id}|cleanup-ids")
    state = State(rx=1, tx=1)
    _finish_guarded(ctx, c, rng, id_rng, state)


def _finish_guarded(ctx: Context, c: Counters, rng, id_rng, state) -> None:
    try:
        _finish(ctx, c, rng, id_rng, state)
    except Exception as exc:  # noqa: BLE001 - the row stays RUNNING, and the reconciler judges it on the next start
        log.error("rehearsal could not be finalised: %s", type(exc).__name__)
