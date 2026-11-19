"""Alert conditions derived from host health. Pure functions: no database, no clock of their own, no ML.

Two condition families:
  * threshold  - a metric is at or above its warning/critical limit (same reasons the console shows, same keys)
  * host_offline - the host has stopped reporting (health status `offline`)

For every evaluation the result is split in two:
  present    - conditions that hold right now (they open or keep an alert open, and may escalate it)
  clearable  - condition keys for which an ABSENT condition is positive evidence of recovery

A key is clearable only when the evidence was actually judged: a threshold series must have a fresh sample that was evaluated,
and host_offline clears only when the host is reporting again. A stale host, an unknown host, or a series with no fresh sample
therefore never clears an alert. Missing telemetry is never treated as healthy.

Replay hosts are never alerted on: they replay stored history, so silence is their normal end.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.monitoring import health as H
from app.monitoring.settings import MonitoringConfig

HOST_OFFLINE_KEY = "host|offline"
RULE_THRESHOLD = "threshold"
RULE_HOST_OFFLINE = "host_offline"


@dataclass(frozen=True)
class Condition:
    key: str
    rule: str
    metric: str | None
    labels: dict
    severity: str  # warning | critical
    value: float | None
    threshold: float | None
    observed_at: datetime | None = None  # threshold: when the breaching sample was observed; offline: the host's last envelope


@dataclass(frozen=True)
class Assessment:
    present: dict[str, Condition] = field(default_factory=dict)
    clearable: frozenset[str] = frozenset()
    observed: dict[str, float] = field(default_factory=dict)  # judged threshold series -> value now (recovery evidence)
    status: str = "unknown"  # the host health status this assessment is based on
    observed_at: dict[str, datetime] = field(default_factory=dict)  # judged threshold series -> when that value was observed
    last_seen: datetime | None = None  # the host's last envelope when this was judged (recovery time for host_offline)


def assess(source: str, last_seen: datetime, now: datetime, samples: list, cfg: MonitoringConfig) -> Assessment | None:
    """None for replay hosts (not alerted on at all). Otherwise the conditions and the keys that may clear."""
    if source == "replay":
        return None
    verdict = H.classify(source, last_seen, now, samples, cfg)
    fresh = H.reporting_status(source, last_seen, now, cfg) == "reporting"

    present: dict[str, Condition] = {}
    clearable: set[str] = set()
    if verdict.status == "offline":
        present[HOST_OFFLINE_KEY] = Condition(HOST_OFFLINE_KEY, RULE_HOST_OFFLINE, None, {}, "critical", None, None, last_seen)
    if fresh:
        clearable.add(HOST_OFFLINE_KEY)
    for reason in verdict.reasons:
        present[reason.key] = Condition(reason.key, RULE_THRESHOLD, reason.metric, dict(reason.labels), reason.level,
                                        float(reason.value), float(reason.threshold), reason.observed_at)
    observed: dict[str, float] = {}
    observed_at: dict[str, datetime] = {}
    if fresh:
        # Threshold series that were judged in this evaluation. Absence of one of them is evidence that it recovered.
        judged = H.evaluated_observations(last_seen, samples, cfg)
        observed = {key: value for key, (value, _) in judged.items()}
        observed_at = {key: when for key, (_, when) in judged.items()}
        clearable |= set(observed)
    return Assessment(present=present, clearable=frozenset(clearable), observed=observed, status=verdict.status,
                      observed_at=observed_at, last_seen=last_seen)
