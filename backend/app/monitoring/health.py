"""Deterministic host health. Pure functions, no database and no ML.

A host's health is a pure function of (source, last_seen, now, latest measurements). Nothing here reads a prediction: an
experimental risk score must never change confirmed health, so it is not even an input.

Rules (thresholds are constants, returned to clients in every list response):
  * offline   - not a replay host AND now - last_seen > OFFLINE_AFTER. Overrides everything else.
  * critical  - any evaluated metric at or above its critical threshold.
  * warning   - any evaluated metric at or above its warning threshold (and none critical).
  * healthy   - at least one metric was evaluated and none crossed a threshold.
  * unknown   - not offline, but no fresh cpu / memory / disk measurement exists to evaluate. Missing metrics are never
                treated as zero, so "nothing to judge" is reported as such instead of "healthy".
REPLAY hosts replay stored history, so silence is the normal end of a replay: they are never offline by the clock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

OFFLINE_AFTER = timedelta(seconds=300)
# A measurement older than this relative to the host's last envelope is stale and not used for classification.
STALE_AFTER = timedelta(seconds=300)

# metric name -> (warning threshold, critical threshold), all percent gauges
THRESHOLDS: dict[str, tuple[float, float]] = {
    "system.cpu.utilization": (85.0, 95.0),
    "system.memory.utilization": (85.0, 95.0),
    "system.disk.utilization": (90.0, 95.0),
}
LEVEL_ORDER = {"healthy": 0, "warning": 1, "critical": 2}


@dataclass(frozen=True)
class Reason:
    metric: str
    labels: dict
    value: float
    threshold: float
    level: str  # "warning" | "critical"


@dataclass(frozen=True)
class Health:
    status: str  # healthy | warning | critical | offline | unknown
    reasons: list[Reason] = field(default_factory=list)


def reporting_status(source: str, last_seen: datetime, now: datetime) -> str:
    if source == "replay":
        return "replay"
    return "reporting" if (now - last_seen) <= OFFLINE_AFTER else "silent"


def classify(source: str, last_seen: datetime, now: datetime, samples: list) -> Health:
    """`samples` are objects with name, labels, value and observed_at (the newest sample per series)."""
    if source != "replay" and (now - last_seen) > OFFLINE_AFTER:
        return Health("offline")
    reasons: list[Reason] = []
    evaluated = 0
    for s in samples:
        rule = THRESHOLDS.get(s.name)
        if rule is None or (last_seen - s.observed_at) > STALE_AFTER:
            continue
        evaluated += 1
        warn, crit = rule
        if s.value >= crit:
            reasons.append(Reason(s.name, s.labels, s.value, crit, "critical"))
        elif s.value >= warn:
            reasons.append(Reason(s.name, s.labels, s.value, warn, "warning"))
    if evaluated == 0:
        return Health("unknown")
    worst = max((LEVEL_ORDER[r.level] for r in reasons), default=0)
    return Health({0: "healthy", 1: "warning", 2: "critical"}[worst], reasons)
