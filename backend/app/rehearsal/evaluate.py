"""Deterministic verdicts for one rehearsal. Pure functions: no database, no clock, no model.

Execution (what happened to the injection):
  COMPLETED     the whole plan was sent and accepted, the fault was confirmed in stored telemetry, and the cleanup/recovery was accepted.
  FAILED        the injection did not produce its fault: no fault telemetry was accepted, or the accepted fault telemetry stayed below the
                CPU warning limit, or envelopes stopped being accepted (abandoned after consecutive failures).
  STOPPED       an operator stopped it after the fault was confirmed, and the recovery telemetry was accepted.
  INCONCLUSIVE  the run cannot be judged: it was interrupted (server restart), the cleanup telemetry was not accepted (the host may still
                report the fault), or the plan was partly not accepted although the fault was confirmed.

Detection (did UptimeTrace notice the fault):
  DETECTED         a new alert for the scenario's metric opened after the confirmed fault onset, within the detection budget.
  LATE_DETECTION   a new alert for the scenario's metric opened after the confirmed fault onset, but later than the budget.
  UNDETECTED       the fault was confirmed, the run was COMPLETED or STOPPED, and no new alert for the metric opened.
  INCONCLUSIVE     no fault was confirmed (a failed injection can never count as detected), a CPU alert was already open before the
                   rehearsal (cannot attribute it), or the run ended too early to judge a missing alert.

Recovery is judged only from the alert: observed when the alert resolved by telemetry (`recovered`) after the recovery telemetry was sent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.rehearsal.catalogue import DETECTION_BUDGET_SECONDS

EXEC_COMPLETED = "COMPLETED"
EXEC_FAILED = "FAILED"
EXEC_STOPPED = "STOPPED"
EXEC_INCONCLUSIVE = "INCONCLUSIVE"
DET_DETECTED = "DETECTED"
DET_LATE = "LATE_DETECTION"
DET_UNDETECTED = "UNDETECTED"
DET_INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class RunFacts:
    planned: int
    sent: int
    accepted: int
    fault_sent: int
    fault_accepted: int
    recovery_accepted: int
    fault_confirmed: bool
    stop_requested: bool
    aborted: bool                 # abandoned after consecutive envelope failures
    interrupted: bool             # the executing process went away before the run finished
    cleanup: str                  # not_needed | recovered | failed
    subject: str = "CPU"        # the metric's name in verdict wording: CPU | Memory


@dataclass(frozen=True)
class AlertFacts:
    opened_at: datetime | None          # when UptimeTrace opened a NEW alert for the fault (None: no new alert)
    resolved_at: datetime | None
    resolution: str | None              # recovered | manual | None
    preexisting_live: bool              # a CPU alert for this host was already open when the rehearsal started


@dataclass(frozen=True)
class Verdict:
    execution: str
    execution_reason: str
    detection: str
    detection_reason: str
    detection_delay_seconds: float | None
    recovery_observed: bool | None
    recovery_seconds: float | None


def _execution(r: RunFacts) -> tuple[str, str]:
    if r.interrupted:
        return EXEC_INCONCLUSIVE, "The run was interrupted before it finished (the server restarted). The cleanup was attempted."
    if r.cleanup == "failed":
        return EXEC_INCONCLUSIVE, ("The cleanup telemetry that ends the fault was not accepted, so the host may still report the fault. "
                                   "Check the host and its alerts.")
    if r.fault_sent > 0 and not r.fault_confirmed:
        if r.fault_accepted == 0:
            return EXEC_FAILED, "Injection failed: no fault telemetry was accepted by UptimeTrace."
        return EXEC_FAILED, (f"Injection failed: the accepted fault telemetry never reached the {r.subject} warning limit, so the fault "
                             "was not observed in stored telemetry.")
    if r.fault_sent == 0:
        if r.stop_requested:
            return EXEC_STOPPED, "Stopped before the fault started. No fault telemetry was sent."
        if r.aborted:
            return EXEC_FAILED, "Injection failed: envelopes were not accepted before the fault started."
        return EXEC_INCONCLUSIVE, "The run ended before the fault phase was reached."
    if r.aborted:
        return EXEC_FAILED, "Abandoned: envelopes were not accepted repeatedly, so the rest of the plan was not sent."
    if r.stop_requested:
        return EXEC_STOPPED, "Stopped by an operator after the fault was confirmed. Recovery telemetry was accepted."
    if r.accepted < r.planned:
        return EXEC_INCONCLUSIVE, (f"The fault was confirmed, but {r.planned - r.accepted} of {r.planned} planned envelopes were not "
                                   "accepted, so the plan did not run as designed.")
    return EXEC_COMPLETED, "The full plan was accepted, the fault was confirmed in stored telemetry, and recovery telemetry was accepted."


def _detection(r: RunFacts, execution: str, fault_onset: datetime | None, alert: AlertFacts) -> tuple[str, str, float | None]:
    if not r.fault_confirmed or fault_onset is None:
        return DET_INCONCLUSIVE, "No fault was confirmed in stored telemetry, so detection cannot be judged.", None
    if alert.opened_at is None:
        if alert.preexisting_live:
            return DET_INCONCLUSIVE, (f"A {r.subject} alert for this host was already open when the rehearsal started, so a new detection cannot be "
                                      "attributed to the fault."), None
        if execution in (EXEC_COMPLETED, EXEC_STOPPED):
            return DET_UNDETECTED, f"The fault was confirmed in telemetry, but no {r.subject} alert opened for it.", None
        return DET_INCONCLUSIVE, "No alert opened, but the run did not finish, so a missing detection cannot be judged.", None
    delay = (alert.opened_at - fault_onset).total_seconds()
    if delay <= DETECTION_BUDGET_SECONDS:
        return DET_DETECTED, (f"A {r.subject} alert opened {delay:.1f} s after the confirmed fault onset, within the "
                              f"{DETECTION_BUDGET_SECONDS:.0f} s budget."), delay
    return DET_LATE, (f"A {r.subject} alert opened {delay:.1f} s after the confirmed fault onset, later than the "
                      f"{DETECTION_BUDGET_SECONDS:.0f} s budget."), delay


def _recovery(r: RunFacts, alert: AlertFacts, recovery_sent_at: datetime | None) -> tuple[bool | None, float | None]:
    if alert.opened_at is None or recovery_sent_at is None or r.recovery_accepted == 0:
        return None, None
    if alert.resolved_at is None:
        return False, None
    if alert.resolution != "recovered":
        return False, None  # resolved by an operator, not by telemetry: recovery was not observed
    if alert.resolved_at < recovery_sent_at:
        return False, None
    return True, (alert.resolved_at - recovery_sent_at).total_seconds()


def evaluate(run: RunFacts, fault_onset: datetime | None, recovery_sent_at: datetime | None, alert: AlertFacts) -> Verdict:
    execution, execution_reason = _execution(run)
    detection, detection_reason, delay = _detection(run, execution, fault_onset, alert)
    observed, recovery_seconds = _recovery(run, alert, recovery_sent_at)
    return Verdict(execution, execution_reason, detection, detection_reason, delay, observed, recovery_seconds)
