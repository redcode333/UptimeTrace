"""Alert lifecycle: open, escalate, acknowledge, recover, resolve. Every change and its timeline event share one transaction.

Concurrency: evaluations of one host are serialised by a transaction-scoped advisory lock, so an ingest-time evaluation and a
sweep never interleave. The partial unique index (migration 0006) is the second guarantee: at most one live alert per condition.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app import errors
from app.alerts.rules import RULE_HOST_OFFLINE, RULE_THRESHOLD, Assessment, Condition, assess
from app.models import Alert, AlertEvent, Host
from app.monitoring import queries as Q
from app.monitoring.settings import MonitoringConfig
from app.monitoring.settings import load as load_settings
from app.registry.service import default_workspace_id

log = logging.getLogger("uptimetrace.alerts")
SYSTEM = "system"
LIVE = ("open", "acknowledged")


@dataclass
class EvaluationResult:
    opened: int = 0
    escalated: int = 0
    recovered: int = 0
    skipped: bool = False


def _event(session: Session, alert: Alert, actor: str, event: str, now: datetime, detail: dict | None = None) -> None:
    session.add(AlertEvent(alert_id=alert.id, workspace_id=alert.workspace_id, occurred_at=now, actor=actor, event=event, detail=detail or {}))


def _lock_host(session: Session, host_id: str) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": "uptimetrace-alerts:" + host_id})


def apply_assessment(session: Session, host: Host, assessment: Assessment, now: datetime) -> EvaluationResult:
    """Bring the host's live alerts in line with the assessment. Caller holds the host lock and owns the transaction."""
    result = EvaluationResult()
    live = {a.condition_key: a for a in session.execute(
        select(Alert).where(Alert.host_pk == host.id, Alert.state.in_(LIVE)).with_for_update()).scalars()}

    for key, cond in assessment.present.items():
        alert = live.get(key)
        if alert is None:
            alert = _open(session, host, cond, now)
            result.opened += 1
            continue
        if alert.severity != cond.severity:
            _event(session, alert, SYSTEM, "severity_changed", now, {"from": alert.severity, "to": cond.severity, "value": cond.value})
            alert.severity = cond.severity
            result.escalated += 1
        alert.value, alert.threshold, alert.last_evaluated_at = cond.value, cond.threshold, now

    for key, alert in live.items():
        if key in assessment.present:
            continue
        if key in assessment.clearable:
            # The recovery value is the one observed now, not the breached value kept on the alert.
            recovered_value = assessment.observed.get(key, alert.value)
            alert.value = recovered_value
            # Recovery evidence: the judged sample for a threshold series; the host reporting again for host_offline.
            if alert.rule == RULE_HOST_OFFLINE:
                alert.recovery_value, alert.recovery_observed_at = None, assessment.last_seen
            else:
                alert.recovery_value, alert.recovery_observed_at = recovered_value, assessment.observed_at.get(key)
            alert.state = "resolved"
            alert.resolved_at, alert.resolved_by, alert.resolution = now, SYSTEM, "recovered"
            alert.last_evaluated_at = now
            _event(session, alert, SYSTEM, "recovered", now, {"reason": "condition_cleared", "value": recovered_value, "threshold": alert.threshold})
            result.recovered += 1
        else:
            # Not judged this time (stale, unknown or unsampled). The alert stays exactly as it was.
            alert.last_evaluated_at = now
    session.flush()
    return result


def _open(session: Session, host: Host, cond: Condition, now: datetime) -> Alert:
    # Trigger evidence is written once, here. A threshold trigger is the breach at `>=` its limit; host_offline has no value.
    is_threshold = cond.rule == RULE_THRESHOLD
    alert = Alert(workspace_id=host.workspace_id, host_pk=host.id, host_id=host.host_id, condition_key=cond.key, rule=cond.rule,
                  metric=cond.metric, labels=cond.labels, severity=cond.severity, state="open", value=cond.value,
                  threshold=cond.threshold, opened_at=now, last_evaluated_at=now,
                  trigger_value=cond.value if is_threshold else None,
                  trigger_threshold=cond.threshold if is_threshold else None,
                  comparison=">=" if is_threshold else None,
                  trigger_observed_at=cond.observed_at)
    session.add(alert)
    session.flush()
    _event(session, alert, SYSTEM, "opened", now, {"severity": cond.severity, "value": cond.value, "threshold": cond.threshold, "rule": cond.rule})
    return alert


def evaluate_host(session: Session, host_id: str, host_rows: list, latest: dict, cfg: MonitoringConfig, now: datetime) -> EvaluationResult:
    """Evaluate one host from already-loaded rows. Returns skipped=True for unknown or replay hosts."""
    host = session.execute(select(Host).where(Host.host_id == host_id)).scalar_one_or_none()
    if host is None or not host_rows:
        return EvaluationResult(skipped=True)
    row = host_rows[0]
    _lock_host(session, host_id)
    assessment = assess(host.kind, row.last_seen, now, latest.get(host_id, []), cfg)
    if assessment is None:
        return EvaluationResult(skipped=True)
    return apply_assessment(session, host, assessment, now)


def evaluate_after_ingest(session_factory: sessionmaker[Session], host_id: str, now: datetime) -> None:
    """Called after an envelope has been committed. Failures are logged (class name only) and never fail ingestion."""
    try:
        with session_factory() as session, session.begin():
            rows = Q.fetch_hosts(session, host_id)
            latest = Q.fetch_latest(session, rows)
            cfg = load_settings(session, default_workspace_id(session)).config
            evaluate_host(session, host_id, rows, latest, cfg, now)
    except Exception as exc:  # noqa: BLE001 - alerting must never turn an accepted envelope into an error
        log.error("alert evaluation after ingest failed: %s", type(exc).__name__)


def sweep_all(session_factory: sessionmaker[Session], now: datetime) -> dict[str, int]:
    """Evaluate every host that has telemetry. Catches time-based changes (a host going silent) that no ingest triggers."""
    totals = {"hosts": 0, "opened": 0, "escalated": 0, "recovered": 0, "failed": 0}
    with session_factory() as session, session.begin():
        rows = Q.fetch_hosts(session)
        latest = Q.fetch_latest(session, rows)
        cfg = load_settings(session, default_workspace_id(session)).config
        for row in rows:
            totals["hosts"] += 1
            try:
                with session.begin_nested():  # one bad host must not roll back the others
                    r = evaluate_host(session, row.host_id, [row], latest, cfg, now)
                totals["opened"] += r.opened
                totals["escalated"] += r.escalated
                totals["recovered"] += r.recovered
            except Exception as exc:  # noqa: BLE001
                totals["failed"] += 1
                log.error("alert evaluation failed for one host: %s", type(exc).__name__)
    return totals


# --------------------------------------------------------------------------------------------------- operator actions
def _alert(session: Session, alert_id: uuid.UUID, lock: bool = False) -> Alert:
    stmt = select(Alert).where(Alert.id == alert_id, Alert.workspace_id == default_workspace_id(session))
    if lock:
        stmt = stmt.with_for_update()
    alert = session.execute(stmt).scalar_one_or_none()
    if alert is None:
        raise errors.ApiError(404, "alert_not_found", "No alert with that id.")
    return alert


def acknowledge(session: Session, alert_id: uuid.UUID, actor: str, now: datetime) -> Alert:
    alert = _alert(session, alert_id, lock=True)
    if alert.state == "resolved":
        raise errors.ApiError(409, "alert_resolved", "This alert is already resolved; it cannot be acknowledged.")
    if alert.state == "open":  # acknowledging twice is a no-op, so the first acknowledgement is what the timeline shows
        alert.state, alert.acknowledged_at, alert.acknowledged_by = "acknowledged", now, actor
        _event(session, alert, actor, "acknowledged", now)
    return alert


def resolve_manually(session: Session, alert_id: uuid.UUID, actor: str, now: datetime) -> Alert:
    """An operator closes an alert. If the breach is still present, the next evaluation opens a NEW alert and says so."""
    alert = _alert(session, alert_id, lock=True)
    if alert.state != "resolved":
        alert.state, alert.resolved_at, alert.resolved_by, alert.resolution = "resolved", now, actor, "manual"
        _event(session, alert, actor, "resolved_manually", now, {"value": alert.value, "threshold": alert.threshold})
    return alert


def events_for(session: Session, alert_id: uuid.UUID) -> list[AlertEvent]:
    return list(session.execute(select(AlertEvent).where(AlertEvent.alert_id == alert_id).order_by(AlertEvent.occurred_at, AlertEvent.id)).scalars())
