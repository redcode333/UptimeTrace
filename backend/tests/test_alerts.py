"""Persistent alerts: deterministic threshold/offline evaluation, deduplication, escalation, recovery, acknowledgement, resolution.

Real PostgreSQL, the real least-privilege role, the real ingestion path (signed by a registry credential) and the real sweep.
The clock is controlled by the test, so stale and offline transitions are exact and repeatable.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.alerts import rules
from app.alerts import service as alert_service
from app.db import make_session_factory
from app.main import create_app
from app.monitoring import health as H
from app.monitoring import settings as cfg_store
from app.monitoring.settings import DEFAULT_CONFIG, Rule
from app.registry.store import RegistryCredentialStore
from tests.clerk_support import OTHER_USER, bearer, verifier
from tests.conftest import FIXED_NOW
from tests.factories import make_envelope

pytestmark = pytest.mark.anyio

T0 = FIXED_NOW
HOST = "alert-host-01"
CPU = "system.cpu.utilization"
MEM = "system.memory.utilization"


def ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def metric(name: str, value: float, when: datetime, unit: str = "percent", labels: dict | None = None) -> dict:
    m = {"name": name, "kind": "gauge", "value": value, "unit": unit, "timestamp": ts(when - timedelta(seconds=0.5))}
    if labels:
        m["labels"] = labels
    return m


# ------------------------------------------------------------------------------------------------ pure rule layer (no DB)
def _sample(name, value, observed, labels=None):
    class S:  # the attributes health.classify reads
        pass
    s = S()
    s.name, s.value, s.observed_at, s.labels = name, value, observed, labels or {}
    return s


def test_rules_breach_is_a_condition_and_is_clearable_only_when_judged():
    last = T0
    breach = assess_(last, [_sample(CPU, 97.0, last)], now=last)
    assert CPU + "|{}" in breach.present and breach.present[CPU + "|{}"].severity == "critical"
    assert breach.present[CPU + "|{}"].rule == "threshold"
    cleared = assess_(last, [_sample(CPU, 20.0, last)], now=last)
    assert not cleared.present and CPU + "|{}" in cleared.clearable   # judged and below the limit: recovery is evidenced


def test_missing_series_is_never_recovery():
    # Memory is judged (normal), CPU has no sample at all: CPU's alert must not be clearable.
    a = assess_(T0, [_sample(MEM, 40.0, T0)], now=T0)
    assert MEM + "|{}" in a.clearable and CPU + "|{}" not in a.clearable


def test_stale_sample_is_not_judged_and_cannot_clear():
    old = T0 - timedelta(seconds=400)               # older than SAMPLE_STALE_AFTER relative to last_seen
    a = assess_(T0, [_sample(CPU, 20.0, old)], now=T0)
    assert CPU + "|{}" not in a.clearable and not a.present


def test_stale_and_offline_hosts_never_clear_threshold_alerts():
    stale_now = T0 + timedelta(seconds=120)          # > stale_after (90 s), <= offline_after (300 s)
    a = assess_(T0, [_sample(CPU, 20.0, T0)], now=stale_now)
    assert a.status == "stale" and not a.clearable and not a.present
    off_now = T0 + timedelta(seconds=400)
    b = assess_(T0, [_sample(CPU, 20.0, T0)], now=off_now)
    assert b.status == "offline" and rules.HOST_OFFLINE_KEY in b.present and rules.HOST_OFFLINE_KEY not in b.clearable


def test_replay_hosts_are_never_alerted_on():
    assert rules.assess("replay", T0, T0 + timedelta(days=3), [_sample(CPU, 99.0, T0)], DEFAULT_CONFIG) is None


def test_series_key_matches_the_console_reason_key():
    labels = {"mount": "/"}
    assert H.series_key("system.disk.utilization", labels) == H.Reason("system.disk.utilization", labels, 1, 1, "warning").key


def assess_(last, samples, now):
    return rules.assess("live", last, now, samples, DEFAULT_CONFIG)


# ------------------------------------------------------------------------------------------------ database and API
@pytest.fixture(autouse=True)
def clean_alerts(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE alert_events, alerts, monitoring_settings, telemetry_envelopes, agent_credentials, hosts, "
                       "audit_events RESTART IDENTITY CASCADE"))
    yield


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def world(app_engine):
    clock = Clock(T0)
    sf = make_session_factory(app_engine)
    store = RegistryCredentialStore(sf)
    app = create_app(app_engine, store, clock=clock, verifier=verifier())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")
    return {"client": client, "clock": clock, "sf": sf, "engine": app_engine}


async def provision(w, host_id=HOST, kind="live"):
    c = w["client"]
    r = await c.post("/api/v1/registry/hosts", json={"host_id": host_id, "kind": kind}, headers=bearer())
    assert r.status_code == 201, r.text
    r = await c.post(f"/api/v1/registry/hosts/{host_id}/credentials", json={}, headers=bearer())
    assert r.status_code == 201, r.text
    return r.json()["token"]


async def send(w, token, host_id, metrics, when):
    # Ingestion validates envelope times against the clock, so the test clock moves with the simulated telemetry.
    w["clock"].at = max(w["clock"].at, when)
    env = make_envelope(host_id=host_id, agent_id="agent-" + host_id, hostname="alert-box", timestamp=ts(when), metrics=metrics)
    r = await w["client"].post("/api/v1/telemetry", json=env, headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 202, r.text
    return r


async def alerts(w, status="all"):
    r = await w["client"].get(f"/api/v1/alerts?status={status}", headers=bearer())
    assert r.status_code == 200, r.text
    return r.json()["alerts"]


async def detail(w, alert_id):
    r = await w["client"].get(f"/api/v1/alerts/{alert_id}", headers=bearer())
    assert r.status_code == 200, r.text
    return r.json()


def sweep(w, when):
    return alert_service.sweep_all(w["sf"], when)


async def test_breach_opens_one_alert_and_repeated_breaches_do_not_duplicate_it(world):
    w = world
    tok = await provision(w)
    await send(w, tok, HOST, [metric(CPU, 97.0, T0)], T0)
    open_ = await alerts(w, "active")
    assert len(open_) == 1
    a = open_[0]
    assert a["severity"] == "critical" and a["state"] == "open"
    assert a["rule"] == "threshold" and a["metric"] == CPU and a["labels"] == {}
    assert a["value"] == 97.0 and a["threshold"] == 95.0
    await send(w, tok, HOST, [metric(CPU, 98.5, T0 + timedelta(seconds=60))], T0 + timedelta(seconds=60))
    await send(w, tok, HOST, [metric(CPU, 99.0, T0 + timedelta(seconds=90))], T0 + timedelta(seconds=90))
    again = await alerts(w, "active")
    assert [x["id"] for x in again] == [a["id"]]               # the same alert, updated in place
    d = await detail(w, a["id"])
    assert [e["event"] for e in d["events"]] == ["opened"]      # no duplicate timeline entries
    with w["engine"].connect() as c:
        assert c.execute(text("SELECT count(*) FROM alerts")).scalar_one() == 1


async def test_escalation_from_warning_to_critical_is_the_same_alert(world):
    w = world
    tok = await provision(w)
    await send(w, tok, HOST, [metric(CPU, 88.0, T0)], T0)
    first = (await alerts(w, "active"))[0]
    assert first["severity"] == "warning"
    await send(w, tok, HOST, [metric(CPU, 97.0, T0 + timedelta(seconds=60))], T0 + timedelta(seconds=60))
    now = await alerts(w, "active")
    assert len(now) == 1 and now[0]["id"] == first["id"] and now[0]["severity"] == "critical"
    events = (await detail(w, first["id"]))["events"]
    assert [e["event"] for e in events] == ["opened", "severity_changed"]
    assert events[1]["detail"]["from"] == "warning" and events[1]["detail"]["to"] == "critical"


async def test_recovery_resolves_the_alert_and_records_the_timeline(world):
    w = world
    tok = await provision(w)
    await send(w, tok, HOST, [metric(CPU, 97.0, T0)], T0)
    aid = (await alerts(w, "active"))[0]["id"]
    later = T0 + timedelta(seconds=60)
    await send(w, tok, HOST, [metric(CPU, 22.0, later)], later)
    assert await alerts(w, "active") == []
    resolved = (await alerts(w, "resolved"))[0]
    assert resolved["id"] == aid and resolved["state"] == "resolved" and resolved["resolution"] == "recovered"
    assert resolved["resolved_by"] == "system" and resolved["resolved_at"] is not None
    events = (await detail(w, aid))["events"]
    assert [e["event"] for e in events] == ["opened", "recovered"]
    assert events[1]["detail"]["value"] == 22.0


async def test_missing_telemetry_never_clears_an_alert(world):
    w = world
    tok = await provision(w)
    await send(w, tok, HOST, [metric(CPU, 97.0, T0)], T0)
    aid = (await alerts(w, "active"))[0]["id"]

    # 1. the host keeps reporting, but CPU is not among the samples: the CPU breach is not judged, so it stays open
    later = T0 + timedelta(seconds=30)
    await send(w, tok, HOST, [metric(MEM, 40.0, later)], later)
    assert (await alerts(w, "active"))[0]["id"] == aid

    # 2. the host goes stale (no envelope for 120 s): thresholds are not judged, so nothing clears
    stale_at = T0 + timedelta(seconds=150)
    w["clock"].at = stale_at
    sweep(w, stale_at)
    assert (await alerts(w, "active"))[0]["id"] == aid
    assert (await alerts(w, "resolved")) == []


async def test_silent_host_raises_host_offline_and_fresh_telemetry_clears_it(world):
    w = world
    tok = await provision(w)
    await send(w, tok, HOST, [metric(CPU, 40.0, T0)], T0)
    silent_for_400 = T0 + timedelta(seconds=400)
    w["clock"].at = silent_for_400
    totals = sweep(w, silent_for_400)
    assert totals["opened"] == 1
    off = [a for a in await alerts(w, "active") if a["rule"] == "host_offline"]
    assert len(off) == 1 and off[0]["severity"] == "critical" and off[0]["host_id"] == HOST
    # Silence did not look healthy: the host page status is offline, and no threshold alert was cleared by silence
    back = silent_for_400 + timedelta(seconds=10)
    w["clock"].at = back
    await send(w, tok, HOST, [metric(CPU, 40.0, back)], back)
    assert [a for a in await alerts(w, "active") if a["rule"] == "host_offline"] == []
    rec = [a for a in await alerts(w, "resolved") if a["rule"] == "host_offline"][0]
    assert rec["resolution"] == "recovered"


async def test_acknowledge_is_recorded_once_and_refused_after_resolution(world):
    w = world
    tok = await provision(w)
    await send(w, tok, HOST, [metric(CPU, 97.0, T0)], T0)
    aid = (await alerts(w, "active"))[0]["id"]
    r1 = await w["client"].post(f"/api/v1/alerts/{aid}/acknowledge", headers=bearer())
    assert r1.status_code == 200 and r1.json()["state"] == "acknowledged" and r1.json()["acknowledged_by"] == "user_allowed_1"
    r2 = await w["client"].post(f"/api/v1/alerts/{aid}/acknowledge", headers=bearer())
    assert r2.status_code == 200 and r2.json()["acknowledged_at"] == r1.json()["acknowledged_at"]
    assert [e["event"] for e in (await detail(w, aid))["events"]] == ["opened", "acknowledged"]
    # an acknowledged breach that recovers is resolved automatically and keeps its acknowledgement
    later = T0 + timedelta(seconds=60)
    await send(w, tok, HOST, [metric(CPU, 10.0, later)], later)
    done = (await alerts(w, "resolved"))[0]
    assert done["acknowledged_by"] == "user_allowed_1" and done["resolution"] == "recovered"
    r3 = await w["client"].post(f"/api/v1/alerts/{aid}/acknowledge", headers=bearer())
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "alert_resolved"


async def test_manual_resolve_then_a_persisting_breach_opens_a_new_alert(world):
    w = world
    tok = await provision(w)
    await send(w, tok, HOST, [metric(CPU, 97.0, T0)], T0)
    first = (await alerts(w, "active"))[0]["id"]
    r = await w["client"].post(f"/api/v1/alerts/{first}/resolve", headers=bearer())
    assert r.status_code == 200 and r.json()["resolution"] == "manual" and r.json()["resolved_by"] == "user_allowed_1"
    again = await w["client"].post(f"/api/v1/alerts/{first}/resolve", headers=bearer())
    assert again.status_code == 200 and again.json()["resolved_at"] == r.json()["resolved_at"]
    later = T0 + timedelta(seconds=60)
    await send(w, tok, HOST, [metric(CPU, 98.0, later)], later)             # the breach is still there
    live = await alerts(w, "active")
    assert len(live) == 1 and live[0]["id"] != first                       # a NEW alert, not a silent reopen
    assert [e["event"] for e in (await detail(w, first))["events"]] == ["opened", "resolved_manually"]


async def test_database_refuses_a_second_live_alert_for_the_same_condition(world):
    w = world
    tok = await provision(w)
    await send(w, tok, HOST, [metric(CPU, 97.0, T0)], T0)
    with w["engine"].connect() as c:
        host_pk = c.execute(text("SELECT id FROM hosts WHERE host_id = :h"), {"h": HOST}).scalar_one()
        wid = c.execute(text("SELECT workspace_id FROM alerts LIMIT 1")).scalar_one()
    with pytest.raises(IntegrityError), w["engine"].begin() as c:
        c.execute(text("INSERT INTO alerts (id, workspace_id, host_pk, host_id, condition_key, rule, severity, state, opened_at, "
                       "last_evaluated_at) VALUES (:i, :w, :h, :hid, :k, 'threshold', 'warning', 'open', now(), now())"),
                  {"i": uuid.uuid4(), "w": wid, "h": host_pk, "hid": HOST, "k": CPU + "|{}"})


async def test_threshold_changes_apply_on_the_next_evaluation(world):
    w = world
    tok = await provision(w)
    cfg = cfg_store.MonitoringConfig(rules={**DEFAULT_CONFIG.rules, CPU: Rule(50.0, 60.0)})
    with w["engine"].begin() as c:
        cfg_store.save(Session(bind=c), wid_of(c), "test", cfg.to_document(), 0, T0)
    await send(w, tok, HOST, [metric(CPU, 55.0, T0)], T0)
    a = (await alerts(w, "active"))[0]
    assert a["severity"] == "warning" and a["threshold"] == 50.0


def wid_of(conn):
    return conn.execute(text("SELECT id FROM workspaces WHERE slug = 'default'")).scalar_one()


async def test_replay_hosts_produce_no_alerts_even_when_breaching(world):
    w = world
    tok = await provision(w, host_id="replay-archive-1", kind="replay")
    await send(w, tok, "replay-archive-1", [metric(CPU, 99.0, T0)], T0)
    assert await alerts(w, "all") == []


async def test_evaluation_failure_never_fails_ingestion(world, monkeypatch):
    w = world
    tok = await provision(w)

    def boom(*a, **k):
        raise RuntimeError("simulated evaluator fault")

    monkeypatch.setattr(alert_service, "evaluate_host", boom)
    await send(w, tok, HOST, [metric(CPU, 97.0, T0)], T0)                 # 202: the envelope is durable
    with w["engine"].connect() as c:
        assert c.execute(text("SELECT count(*) FROM telemetry_envelopes")).scalar_one() == 1
        assert c.execute(text("SELECT count(*) FROM alerts")).scalar_one() == 0


async def test_one_failing_host_does_not_stop_the_sweep(world, monkeypatch):
    w = world
    tok_a = await provision(w, host_id="alert-host-a")
    tok_b = await provision(w, host_id="alert-host-b")
    await send(w, tok_a, "alert-host-a", [metric(CPU, 97.0, T0)], T0)
    await send(w, tok_b, "alert-host-b", [metric(CPU, 97.0, T0)], T0)
    real = alert_service.evaluate_host

    def flaky(session, host_id, *a, **k):
        if host_id == "alert-host-a":
            raise RuntimeError("simulated")
        return real(session, host_id, *a, **k)

    later = T0 + timedelta(seconds=400)
    monkeypatch.setattr(alert_service, "evaluate_host", flaky)
    totals = sweep(w, later)
    assert totals["failed"] == 1 and totals["hosts"] == 2
    assert any(a["host_id"] == "alert-host-b" and a["rule"] == "host_offline" for a in await alerts(w, "active"))


async def test_alerts_api_is_browser_only_and_workspace_scoped(world):
    w = world
    tok = await provision(w)
    await send(w, tok, HOST, [metric(CPU, 97.0, T0)], T0)
    aid = (await alerts(w, "active"))[0]["id"]
    anon = await w["client"].get("/api/v1/alerts")
    assert anon.status_code == 401
    agent = await w["client"].get("/api/v1/alerts", headers={"authorization": f"Bearer {tok}"})
    assert agent.status_code == 401                                        # an agent credential cannot read alerts
    stranger = await w["client"].get("/api/v1/alerts", headers=bearer(sub=OTHER_USER))
    assert stranger.status_code == 403
    assert (await w["client"].post(f"/api/v1/alerts/{aid}/acknowledge", headers=bearer(sub=OTHER_USER))).status_code == 403
    missing = await w["client"].post(f"/api/v1/alerts/{uuid.uuid4()}/acknowledge", headers=bearer())
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "alert_not_found"
    assert (await w["client"].get(f"/api/v1/alerts?host_id={HOST}", headers=bearer())).json()["total"] == 1


async def test_alert_tables_are_not_writable_by_the_application_role_beyond_its_grants(app_engine):
    with app_engine.connect() as c:
        # the application role may not delete alerts or rewrite identity columns
        assert not c.execute(text("SELECT has_table_privilege('uptimetrace_app', 'alerts', 'DELETE')")).scalar_one()
        assert not c.execute(text("SELECT has_column_privilege('uptimetrace_app', 'alerts', 'host_id', 'UPDATE')")).scalar_one()
        assert not c.execute(text("SELECT has_table_privilege('uptimetrace_app', 'alert_events', 'UPDATE')")).scalar_one()
