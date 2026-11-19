"""Alert investigation: a bounded evidence view, and a persisted investigation record derived from it.

Nothing here infers a cause. The record separates three things:
  * facts        - observed values and recorded events, each with its source;
  * conclusions  - fixed rules applied to those facts (e.g. "the trigger condition was met");
  * not_established - what the evidence cannot show (root cause, unrecorded times, operator-only resolution).

Determinism: `derive(evidence)` is a pure function of the stored evidence snapshot. The record stores that snapshot and a SHA-256
digest of the whole body, so anyone can re-derive the conclusions and check them. No language model is involved.

Bounds: the telemetry windows are fixed around the trigger (and the recovery sample), each `window` minutes either side, and
related alerts are paged. No query is unbounded.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import errors
from app.alerts import service as AS
from app.alerts.rules import RULE_THRESHOLD
from app.models import Alert, InvestigationReport
from app.monitoring import queries as Q
from app.monitoring import service as MS
from app.monitoring.settings import load as load_settings
from app.registry.service import default_workspace_id

SCHEMA_VERSION = "investigation.v1"
WINDOW_DEFAULT_MIN = 30
WINDOW_MIN_MIN = 5
WINDOW_MAX_MIN = 120
RELATED_PAGE_MAX = 100
EVENTS_MAX = 200
RELATED_NOTE = "Related alerts share a host and overlap in time. That is correlation only."


@dataclass(frozen=True)
class Window:
    minutes: int

    @property
    def delta(self) -> timedelta:
        return timedelta(minutes=self.minutes)


def parse_window(minutes: int | None) -> Window:
    m = WINDOW_DEFAULT_MIN if minutes is None else minutes
    if not (WINDOW_MIN_MIN <= m <= WINDOW_MAX_MIN):
        raise errors.invalid_request([errors.ErrorDetail("window_minutes", f"Between {WINDOW_MIN_MIN} and {WINDOW_MAX_MIN} minutes.")])
    return Window(m)


def iso(t: datetime | None) -> str | None:
    if t is None:
        return None
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def _label(metric: str | None, labels: dict) -> str:
    base = metric or "metric"
    mount = labels.get("mount")
    return f"{base} ({mount})" if mount else base


def _load_alert(session: Session, alert_id: uuid.UUID) -> Alert:
    # The same workspace gate as the alert API: an alert in another workspace is indistinguishable from a missing one.
    alert = session.execute(select(Alert).where(Alert.id == alert_id, Alert.workspace_id == default_workspace_id(session))).scalar_one_or_none()
    if alert is None:
        raise errors.ApiError(404, "alert_not_found", "No alert with that id.")
    return alert


def _series_window(session: Session, alert: Alert, start: datetime, end: datetime, name: str) -> dict:
    """Points of the alert's own series (matching label set) in [start, end]. Gaps are null, never zero."""
    resp = MS.load_series(session, alert.host_id, alert.metric, start, end, None, "avg")
    points: list[dict] = []
    if resp.kind is not None:
        for s in resp.series:
            if s.labels == (alert.labels or {}):
                points = [{"t": iso(p.t), "value": p.value, "samples": p.samples} for p in s.points]
                break
    return {"window": name, "start": iso(start), "end": iso(end), "step_seconds": resp.step_seconds,
            "metric": alert.metric, "labels": alert.labels or {}, "points": points}


def build_view(session: Session, alert: Alert, now: datetime, window: Window, related_limit: int, related_offset: int) -> dict:
    cfg_stored = load_settings(session, default_workspace_id(session))
    cfg = cfg_stored.config
    d = window.delta
    threshold = alert.rule == RULE_THRESHOLD

    unavailable = []
    if threshold and alert.trigger_value is None:
        unavailable.append("trigger_value")
    if alert.trigger_observed_at is None:
        unavailable.append("trigger_observed_at")
    if alert.state == "resolved" and alert.resolution == "recovered" and alert.recovery_observed_at is None:
        unavailable.append("recovery_observed_at")

    recovered_by_telemetry = alert.state == "resolved" and alert.resolution == "recovered"
    trigger = {
        "rule": alert.rule, "metric": alert.metric, "labels": alert.labels or {}, "severity": alert.severity,
        "comparison": alert.comparison, "threshold": alert.trigger_threshold, "trigger_value": alert.trigger_value,
        "trigger_observed_at": iso(alert.trigger_observed_at), "first_fired_at": iso(alert.opened_at),
        "last_evaluated_at": iso(alert.last_evaluated_at), "unavailable": unavailable,
    }
    recovery = {
        "recovered": recovered_by_telemetry, "resolution": alert.resolution, "resolved_by": alert.resolved_by,
        "resolved_at": iso(alert.resolved_at), "value": alert.recovery_value if alert.rule == RULE_THRESHOLD else None,
        "observed_at": iso(alert.recovery_observed_at),
    }

    rows = Q.fetch_hosts(session, alert.host_id)
    host: dict = {"host_id": alert.host_id, "known": bool(rows), "current": None}
    if rows:
        latest = Q.fetch_latest(session, rows)
        built = MS.build_host(rows[0], latest[alert.host_id], now, cfg, with_metrics=False)
        host["current"] = built.model_dump(mode="json")

    series: list[dict] = []
    if threshold and alert.metric:
        series.append(_series_window(session, alert, alert.opened_at - d, alert.opened_at + d, "around_trigger"))
        if recovered_by_telemetry and alert.recovery_observed_at is not None:
            r = alert.recovery_observed_at
            series.append(_series_window(session, alert, r - d, r + d, "around_recovery"))

    events = [{"event": e.event, "occurred_at": iso(e.occurred_at), "actor": e.actor, "detail": e.detail}
              for e in AS.events_for(session, alert.id)][:EVENTS_MAX]

    lo, hi = alert.opened_at - d, alert.opened_at + d
    base = select(Alert).where(Alert.workspace_id == alert.workspace_id, Alert.host_id == alert.host_id, Alert.id != alert.id,
                               Alert.opened_at >= lo, Alert.opened_at <= hi)
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    related_rows = session.execute(base.order_by(Alert.opened_at, Alert.id).limit(related_limit).offset(related_offset)).scalars().all()
    related = {
        "window_minutes": window.minutes, "total": total, "limit": related_limit, "offset": related_offset, "note": RELATED_NOTE,
        "items": [{"id": str(a.id), "rule": a.rule, "metric": a.metric, "labels": a.labels or {}, "severity": a.severity,
                   "state": a.state, "opened_at": iso(a.opened_at), "resolution": a.resolution} for a in related_rows],
    }

    settings = {"version": cfg_stored.version,
                "warning_limit": cfg.rules[alert.metric].warning if threshold and alert.metric in cfg.rules else None,
                "critical_limit": cfg.rules[alert.metric].critical if threshold and alert.metric in cfg.rules else None,
                "stale_after_seconds": cfg.stale_after.total_seconds(), "offline_after_seconds": cfg.offline_after.total_seconds()}

    return {
        "as_of": iso(now),
        "alert": {"id": str(alert.id), "host_id": alert.host_id, "state": alert.state, "severity": alert.severity,
                  "rule": alert.rule, "metric": alert.metric, "labels": alert.labels or {}, "condition_key": alert.condition_key,
                  "acknowledged_by": alert.acknowledged_by, "acknowledged_at": iso(alert.acknowledged_at),
                  "resolved_by": alert.resolved_by, "resolution": alert.resolution},
        "trigger": trigger, "recovery": recovery, "host": host, "series": series,
        "series_note": None if threshold else "Host offline alerts have no metric series; the host's last telemetry time is the evidence.",
        "events": events, "related": related, "settings": settings, "window_minutes": window.minutes,
    }


# ----------------------------------------------------------------------------------------- deterministic derivation
def derive(ev: dict) -> dict:
    """Facts, conclusions and not_established items from an evidence snapshot. Pure: same input, same output."""
    facts: list[dict] = []
    conclusions: list[dict] = []
    gaps: list[str] = []

    def fact(text: str, source: str) -> str:
        fid = f"F{len(facts) + 1}"
        facts.append({"id": fid, "text": text, "source": source})
        return fid

    a, t, rec, s = ev["alert"], ev["trigger"], ev["recovery"], ev["settings"]
    threshold = a["rule"] == RULE_THRESHOLD
    label = _label(a["metric"], a["labels"])
    fact(f"Alert {a['id']} is a {a['rule']} condition on host {a['host_id']} with severity {a['severity']}; state {a['state']}.", "alert")
    fact(f"First fired at {t['first_fired_at']}; last evaluated at {t['last_evaluated_at']}.", "alert")

    f_trigger = None
    if threshold and t["trigger_value"] is not None:
        f_trigger = fact(f"Trigger: {label} read {t['trigger_value']:.2f} against the {t['comparison']} {t['threshold']:.2f} "
                         f"{a['severity']} limit"
                         + (f", observed at {t['trigger_observed_at']}." if t["trigger_observed_at"] else ". The observation time is unavailable."),
                         "trigger")
        conclusions.append({"id": "C1", "basis": [f_trigger],
                            "text": f"The trigger condition was met: {label} {t['trigger_value']:.2f} is at or above the "
                                    f"{a['severity']} limit {t['threshold']:.2f} (deterministic threshold rule)."})
    if not threshold and t["trigger_observed_at"]:
        f_trigger = fact(f"The host's last telemetry before the alert was at {t['trigger_observed_at']}; the host was judged offline "
                         f"after {s['offline_after_seconds']:.0f} s without telemetry.", "trigger")

    for e in ev["events"]:
        fact(f"Timeline: {e['event']} at {e['occurred_at']} by {e['actor']}.", "event")

    for w in ev["series"]:
        before = [p for p in w["points"] if p["value"] is not None and p["t"] < t["first_fired_at"]]
        after = [p for p in w["points"] if p["value"] is not None and p["t"] >= t["first_fired_at"]]
        if w["window"] == "around_trigger":
            fact(f"Telemetry for {label}: {len(before)} populated buckets before the alert opened and {len(after)} from the opening "
                 f"onward, within ±{ev['window_minutes']} minutes.", "telemetry")
            if not before and not after:
                gaps.append("No telemetry for this alert's metric is available in the window around the trigger.")
        else:
            fact(f"Telemetry for {label} around the recovery sample: {len(w['points'])} buckets in the window.", "telemetry")

    if rec["recovered"] and threshold and rec["value"] is not None:
        f_rec = fact(f"Recovery: {label} read {rec['value']:.2f} at {rec['observed_at'] or 'an unrecorded time'}.", "recovery")
        if s["warning_limit"] is not None:
            conclusions.append({"id": f"C{len(conclusions) + 1}", "basis": [f_rec],
                                "text": f"Recovery is evidenced by telemetry: {label} {rec['value']:.2f} is below the warning limit "
                                        f"{s['warning_limit']:.2f} in settings version {s['version']}."})
    if rec["recovered"] and not threshold and rec["observed_at"]:
        f_rec = fact(f"Recovery: host reporting resumed at {rec['observed_at']}.", "recovery")
        conclusions.append({"id": f"C{len(conclusions) + 1}", "basis": [f_rec],
                            "text": "Recovery is evidenced: the host reported telemetry again after the offline condition."})
    if a["state"] == "resolved" and a["resolution"] == "manual":
        f_man = fact(f"Resolved manually by {a['resolved_by']} at {a['resolved_at']}.", "recovery")
        conclusions.append({"id": f"C{len(conclusions) + 1}", "basis": [f_man],
                            "text": "Recovery was not evidenced by telemetry; the alert was closed by an operator."})
    if a["state"] != "resolved":
        f_open = fact(f"The alert is {a['state']} as of {ev['as_of']}.", "alert")
        conclusions.append({"id": f"C{len(conclusions) + 1}", "basis": [f_open], "text": "Recovery has not been evidenced."})

    if threshold and s["warning_limit"] is not None:
        fact(f"Monitoring settings version {s['version']}: warning limit for {a['metric']} is {s['warning_limit']:.2f}, "
             f"critical {s['critical_limit']:.2f}.", "settings")

    host = ev["host"]
    if host.get("current"):
        cur = host["current"]
        fact(f"Current host state as of {ev['as_of']}: source {cur.get('source')}, health {cur['health']['status']}, "
             f"reporting {cur.get('reporting_status')}.", "host")
    else:
        fact("The host has no telemetry on record.", "host")

    rel = ev["related"]
    fact(f"{rel['total']} other alert(s) on this host opened within ±{ev['window_minutes']} minutes; {len(rel['items'])} listed "
         f"(offset {rel['offset']}).", "related")
    for r in rel["items"]:
        fact(f"Related alert {r['id']}: {r['rule']} {_label(r['metric'], r['labels'])}, severity {r['severity']}, opened {r['opened_at']}, "
             f"state {r['state']}.", "related")
    if rel["total"]:
        conclusions.append({"id": f"C{len(conclusions) + 1}", "basis": [f["id"] for f in facts if f["source"] == "related"],
                            "text": f"{rel['total']} related alert(s) overlapped in time on this host. This is temporal correlation only; "
                                    "the evidence does not establish that any one caused another."})

    not_established = [
        "Root cause is not established. UptimeTrace records host metrics and host liveness only; logs, process, deployment and "
        "network data are not collected.",
    ]
    if threshold and t["trigger_observed_at"] is None:
        not_established.append("The time the breaching sample was observed is unavailable for this alert (it opened before that evidence was recorded).")
    if threshold and rec["recovered"]:
        not_established.append(f"The warning limit used to judge recovery is the current setting (version {s['version']} at report time); "
                               "the limit in force when the alert recovered was not stored separately.")
    if a["state"] == "resolved" and a["resolution"] == "manual":
        not_established.append("Telemetry does not show that the condition cleared; the alert was closed by an operator.")
    if a["state"] != "resolved":
        not_established.append(f"Recovery had not occurred as of {ev['as_of']}.")
    not_established.extend(gaps)
    if ev.get("trigger") and t.get("unavailable"):
        not_established.append("Unavailable evidence: " + ", ".join(t["unavailable"]) + ".")
    return {"facts": facts, "conclusions": conclusions, "not_established": not_established}


def _body(evidence: dict, created_at: datetime, actor: str) -> dict:
    derived = derive(evidence)
    return {"schema_version": SCHEMA_VERSION, "generated_at": iso(created_at), "generated_by": actor, "evidence": evidence, **derived}


def verify(record: InvestigationReport) -> dict:
    """Recompute the digest and re-derive the conclusions from the stored evidence. Both must match."""
    body = record.body
    digest_ok = digest(body) == record.evidence_digest
    rederived = derive(body["evidence"])
    derive_ok = all(rederived[k] == body.get(k) for k in ("facts", "conclusions", "not_established"))
    return {"digest_matches": digest_ok, "rederivation_matches": derive_ok, "verified": digest_ok and derive_ok}


def report_out(record: InvestigationReport) -> dict:
    return {"id": str(record.id), "alert_id": str(record.alert_id), "created_at": iso(record.created_at), "created_by": record.created_by,
            "schema_version": record.schema_version, "evidence_digest": record.evidence_digest, **verify(record), "body": record.body}


def existing_report(session: Session, alert_id: uuid.UUID) -> InvestigationReport | None:
    _load_alert(session, alert_id)  # workspace gate first
    return session.execute(select(InvestigationReport).where(InvestigationReport.alert_id == alert_id)).scalar_one_or_none()


def create_report(session: Session, alert_id: uuid.UUID, actor: str, now: datetime, window: Window) -> tuple[InvestigationReport, bool]:
    """Create the record once. Returns (record, created). A later call returns the stored record unchanged."""
    found = existing_report(session, alert_id)
    if found is not None:
        return found, False
    alert = _load_alert(session, alert_id)
    view = build_view(session, alert, now, window, RELATED_PAGE_MAX, 0)
    body = _body(view, now, actor)
    record = InvestigationReport(alert_id=alert.id, workspace_id=alert.workspace_id, created_at=now, created_by=actor,
                                 schema_version=SCHEMA_VERSION, evidence_digest=digest(body), body=body)
    try:
        with session.begin_nested():  # savepoint: a losing concurrent insert rolls back only itself
            session.add(record)
            session.flush()
    except IntegrityError:  # a concurrent request created it first: return that record
        return existing_report(session, alert_id), False
    return record, True
