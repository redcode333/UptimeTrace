"""Deterministic host health. Pure functions, no database and no ML.

Inputs: (source, last_seen, now, newest sample per series, MonitoringConfig). Nothing here reads a prediction.

Two separate questions, answered in this order:
  1. FRESHNESS - is the data current enough to judge anything?   (age = now - last_seen, the newest envelope's timestamp)
  2. THRESHOLDS - is there a concern in the current data?        (cfg.rules; see settings.py)

Transition rules (age in seconds; stale_after < offline_after, both configurable; defaults 90 / 300). All boundaries are
inclusive on the fresher side, so a host exactly `stale_after` old is still evaluated:

    age <= stale_after                     -> evaluate thresholds:
        no fresh cpu/memory/disk sample        -> UNKNOWN   (insufficient evidence; missing is never zero)
        any metric >= critical                 -> CRITICAL  (category DEGRADED)
        else any metric >= warning             -> WARNING   (category DEGRADED)
        else                                   -> HEALTHY
    stale_after < age <= offline_after     -> STALE         (data too old to judge; last values are NOT evaluated)
    age > offline_after                    -> OFFLINE
    replay hosts replay stored history, so silence is their normal end: never STALE/OFFLINE by the clock.
A host that was registered but has never reported is UNKNOWN (see registry/service.py); an unregistered host does not exist for health.

`category` is the coarse grouping used by consumers that do not care about severity:
    healthy -> HEALTHY, warning|critical -> DEGRADED, stale -> STALE, offline -> OFFLINE, unknown -> UNKNOWN.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from app.monitoring.settings import DEFAULT_CONFIG, SAMPLE_STALE_AFTER, MonitoringConfig

# Kept for callers/tests that import the defaults.
OFFLINE_AFTER = DEFAULT_CONFIG.offline_after
STALE_AFTER = SAMPLE_STALE_AFTER          # SAMPLE staleness (relative to the host's last envelope), not host staleness
HOST_STALE_AFTER = DEFAULT_CONFIG.stale_after
THRESHOLDS: dict[str, tuple[float, float]] = {m: (r.warning, r.critical) for m, r in DEFAULT_CONFIG.rules.items()}
LEVEL_ORDER = {"healthy": 0, "warning": 1, "critical": 2}
CATEGORY = {"healthy": "healthy", "warning": "degraded", "critical": "degraded", "stale": "stale", "offline": "offline",
            "unknown": "unknown"}


@dataclass(frozen=True)
class Reason:
    metric: str
    labels: dict
    value: float
    threshold: float
    level: str  # "warning" | "critical"
    observed_at: datetime | None = None  # when the breaching sample was observed (alert trigger evidence)

    @property
    def key(self) -> str:
        """Stable identity of the condition (metric + label set), independent of level or value. M2 alert dedup keys on this."""
        return self.metric + "|" + json.dumps(self.labels, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Health:
    status: str  # healthy | warning | critical | stale | offline | unknown
    reasons: list[Reason] = field(default_factory=list)

    @property
    def category(self) -> str:
        return CATEGORY[self.status]


def reporting_status(source: str, last_seen: datetime, now: datetime, cfg: MonitoringConfig = DEFAULT_CONFIG) -> str:
    if source == "replay":
        return "replay"
    age = now - last_seen
    if age > cfg.offline_after:
        return "silent"
    return "reporting" if age <= cfg.stale_after else "stale"


def is_connected(source: str, last_seen: datetime, now: datetime, cfg: MonitoringConfig = DEFAULT_CONFIG) -> bool:
    """Connected infrastructure: a LIVE host whose newest envelope is within offline_after. Same boundary as `classify`
    (age equal to offline_after is not offline, so it is connected). Synthetic and replay hosts are never connected."""
    return source == "live" and (now - last_seen) <= cfg.offline_after


def evaluate_thresholds(last_seen: datetime, samples: list, cfg: MonitoringConfig = DEFAULT_CONFIG) -> tuple[int, list[Reason]]:
    """(number of metrics that could be evaluated, conditions found). Samples older than SAMPLE_STALE_AFTER are skipped, not zeroed."""
    reasons: list[Reason] = []
    evaluated = 0
    for s in samples:
        rule = cfg.rules.get(s.name)
        if rule is None or (last_seen - s.observed_at) > SAMPLE_STALE_AFTER:
            continue
        evaluated += 1
        if s.value >= rule.critical:
            reasons.append(Reason(s.name, s.labels, s.value, rule.critical, "critical", s.observed_at))
        elif s.value >= rule.warning:
            reasons.append(Reason(s.name, s.labels, s.value, rule.warning, "warning", s.observed_at))
    return evaluated, reasons


def series_key(name: str, labels: dict) -> str:
    """Identity of a (metric, label set) pair. Reason.key and the alert condition key both use this."""
    return name + "|" + json.dumps(labels, sort_keys=True, separators=(",", ":"))


def evaluated_observations(last_seen: datetime, samples: list, cfg: MonitoringConfig = DEFAULT_CONFIG) -> dict[str, tuple[float, datetime]]:
    """Threshold series that WERE judged in this evaluation: key -> (observed value, observed_at).

    Alerts use this to decide whether a missing breach is positive evidence of recovery: a series that was not judged
    (missing, or older than SAMPLE_STALE_AFTER) can never clear an alert, because missing telemetry is never healthy."""
    return {series_key(s.name, s.labels): (float(s.value), s.observed_at) for s in samples
            if cfg.rules.get(s.name) is not None and (last_seen - s.observed_at) <= SAMPLE_STALE_AFTER}


def evaluated_samples(last_seen: datetime, samples: list, cfg: MonitoringConfig = DEFAULT_CONFIG) -> dict[str, float]:
    """key -> observed value for the series judged in this evaluation (see evaluated_observations)."""
    return {key: value for key, (value, _) in evaluated_observations(last_seen, samples, cfg).items()}


def evaluated_series(last_seen: datetime, samples: list, cfg: MonitoringConfig = DEFAULT_CONFIG) -> set[str]:
    return set(evaluated_samples(last_seen, samples, cfg))


def classify(source: str, last_seen: datetime, now: datetime, samples: list, cfg: MonitoringConfig = DEFAULT_CONFIG) -> Health:
    """`samples` are objects with name, labels, value and observed_at (the newest sample per series)."""
    if source != "replay":
        age = now - last_seen
        if age > cfg.offline_after:
            return Health("offline")
        if age > cfg.stale_after:
            return Health("stale")
    evaluated, reasons = evaluate_thresholds(last_seen, samples, cfg)
    if evaluated == 0:
        return Health("unknown")
    worst = max((LEVEL_ORDER[r.level] for r in reasons), default=0)
    return Health({0: "healthy", 1: "warning", 2: "critical"}[worst], reasons)
