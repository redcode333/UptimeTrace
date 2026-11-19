"""Configurable monitoring thresholds and freshness limits. Pure parsing/validation here; storage is one JSON document per workspace
(`monitoring_settings`, migration 0005) with optimistic versioning.

Thresholds (what is a concern) are deliberately separate from freshness (is the data current enough to judge): see health.py.
Evaluation model today: the NEWEST sample of each series that is no older than SAMPLE_STALE_AFTER relative to the host's last envelope.
Sustained-duration windows ("above 90% for 5 minutes") need history and belong to the M2 alert lifecycle, not to health; they are
intentionally not part of this document so that no setting exists that the code does not honour.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import errors
from app.errors import ErrorDetail

SAMPLE_STALE_AFTER = timedelta(seconds=300)   # a sample this much older than the host's last envelope is not used (fixed)
RULE_METRICS = ("system.cpu.utilization", "system.memory.utilization", "system.disk.utilization")
STALE_RANGE = (10, 3600)          # seconds
OFFLINE_MAX = 86400


@dataclass(frozen=True)
class Rule:
    warning: float
    critical: float


def _default_rules() -> dict[str, Rule]:
    return {"system.cpu.utilization": Rule(85.0, 95.0), "system.memory.utilization": Rule(85.0, 95.0),
            "system.disk.utilization": Rule(90.0, 95.0)}


@dataclass(frozen=True)
class MonitoringConfig:
    stale_after: timedelta = timedelta(seconds=90)      # no envelope for longer than this => STALE
    offline_after: timedelta = timedelta(seconds=300)   # no envelope for longer than this => OFFLINE
    rules: dict[str, Rule] = field(default_factory=_default_rules)

    def to_document(self) -> dict:
        return {"freshness": {"stale_after_seconds": int(self.stale_after.total_seconds()),
                              "offline_after_seconds": int(self.offline_after.total_seconds())},
                "rules": {m: {"warning": r.warning, "critical": r.critical} for m, r in self.rules.items()}}


DEFAULT_CONFIG = MonitoringConfig()


def parse_document(doc: object) -> MonitoringConfig:
    """Strict validation. Unknown keys, wrong types and inconsistent values are 422s that name the offending field."""
    problems: list[ErrorDetail] = []

    def bad(path: str, msg: str):
        problems.append(ErrorDetail(path, msg))

    if not isinstance(doc, dict) or set(doc) != {"freshness", "rules"}:
        raise errors.invalid_request([ErrorDetail("config", "Exactly the keys 'freshness' and 'rules' are required.")])
    fr, rules = doc["freshness"], doc["rules"]

    def num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    stale = offline = None
    if not isinstance(fr, dict) or set(fr) != {"stale_after_seconds", "offline_after_seconds"}:
        bad("freshness", "Exactly 'stale_after_seconds' and 'offline_after_seconds' are required.")
    else:
        stale, offline = fr["stale_after_seconds"], fr["offline_after_seconds"]
        if not (isinstance(stale, int) and not isinstance(stale, bool) and STALE_RANGE[0] <= stale <= STALE_RANGE[1]):
            bad("freshness.stale_after_seconds", f"Integer {STALE_RANGE[0]}..{STALE_RANGE[1]}.")
        if not (isinstance(offline, int) and not isinstance(offline, bool) and offline <= OFFLINE_MAX):
            bad("freshness.offline_after_seconds", f"Integer, at most {OFFLINE_MAX}.")
        elif isinstance(stale, int) and offline <= stale:
            bad("freshness.offline_after_seconds", "Must be greater than stale_after_seconds.")
    parsed: dict[str, Rule] = {}
    if not isinstance(rules, dict) or set(rules) != set(RULE_METRICS):
        bad("rules", f"Exactly these metrics are required: {list(RULE_METRICS)}.")
    else:
        for metric, r in rules.items():
            if not isinstance(r, dict) or set(r) != {"warning", "critical"} or not (num(r["warning"]) and num(r["critical"])):
                bad(f"rules.{metric}", "Exactly numeric 'warning' and 'critical' are required.")
            elif not (0 < r["warning"] < r["critical"] <= 100):
                bad(f"rules.{metric}", "Requires 0 < warning < critical <= 100 (percent).")
            else:
                parsed[metric] = Rule(float(r["warning"]), float(r["critical"]))
    if problems:
        raise errors.invalid_request(problems)
    assert isinstance(stale, int) and isinstance(offline, int)  # established by the validation above
    return MonitoringConfig(timedelta(seconds=stale), timedelta(seconds=offline), parsed)


@dataclass(frozen=True)
class StoredConfig:
    config: MonitoringConfig
    version: int                     # 0 = built-in defaults, nothing stored yet
    updated_at: datetime | None
    updated_by: str | None


def load(session: Session, workspace_id: uuid.UUID) -> StoredConfig:
    row = session.execute(text("SELECT config, version, updated_at, updated_by FROM monitoring_settings WHERE workspace_id = :w"),
                          {"w": workspace_id}).first()
    if row is None:
        return StoredConfig(DEFAULT_CONFIG, 0, None, None)
    return StoredConfig(parse_document(row.config), row.version, row.updated_at, row.updated_by)


def save(session: Session, workspace_id: uuid.UUID, actor: str, doc: object, expected_version: int, now: datetime) -> StoredConfig:
    """Compare-and-set on `version` so two editors cannot silently overwrite each other (409)."""
    import json
    cfg = parse_document(doc)
    payload = json.dumps(cfg.to_document())
    if expected_version == 0:
        done = session.execute(text(
            "INSERT INTO monitoring_settings (workspace_id, config, version, updated_at, updated_by) "
            "VALUES (:w, CAST(:c AS jsonb), 1, :n, :a) ON CONFLICT (workspace_id) DO NOTHING RETURNING version"),
            {"w": workspace_id, "c": payload, "n": now, "a": actor}).first()
    else:
        done = session.execute(text(
            "UPDATE monitoring_settings SET config = CAST(:c AS jsonb), version = version + 1, updated_at = :n, updated_by = :a "
            "WHERE workspace_id = :w AND version = :v RETURNING version"),
            {"w": workspace_id, "c": payload, "n": now, "a": actor, "v": expected_version}).first()
    if done is None:
        raise errors.ApiError(409, "version_conflict", "The settings changed since you loaded them; reload and retry.")
    return StoredConfig(cfg, done.version, now, actor)
