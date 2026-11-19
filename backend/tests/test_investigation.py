"""Alert investigation: trigger and recovery evidence, the bounded investigation view, the persisted record, and isolation.

Real PostgreSQL, the real least-privilege role, the real signed ingestion path and the real evaluator. Telemetry is sent in
chronological order with the test clock following it, as in test_alerts. Nothing here is mocked.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.clerk_support import OTHER_USER, bearer
from tests.test_alerts import CPU, HOST, MEM, T0, alerts, detail, metric, provision, send, sweep, ts, world  # noqa: F401 (fixture)

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def clean_investigation(engine):
    """Owner-role reset before each test: alerts, reports, telemetry, hosts, and any second workspace."""
    with engine.begin() as c:
        c.execute(text("TRUNCATE investigation_reports, alert_events, alerts, monitoring_settings, telemetry_envelopes, "
                       "agent_credentials, hosts, audit_events RESTART IDENTITY CASCADE"))
        c.execute(text("DELETE FROM workspaces WHERE slug <> 'default'"))
    yield


def mins(n: float):
    return T0 + timedelta(minutes=n)


async def run_breach_and_recovery(w):
    """Real telemetry: normal before, CPU 97 + memory 90 at T0 (both open), still breaching, then both recover."""
    # The test clock starts at T0 and never moves back, so start it at the first sample: each sample is then evaluated at its own
    # time. (Left at T0, the 20-minute-old first sample would correctly raise a host_offline alert.)
    w["clock"].at = mins(-20)
    tok = await provision(w)
    await send(w, tok, HOST, [metric(CPU, 22.0, mins(-20)), metric(MEM, 40.0, mins(-20))], mins(-20))
    await send(w, tok, HOST, [metric(CPU, 25.0, mins(-10)), metric(MEM, 42.0, mins(-10))], mins(-10))
    await send(w, tok, HOST, [metric(CPU, 97.0, T0), metric(MEM, 90.0, T0)], T0)
    await send(w, tok, HOST, [metric(CPU, 98.0, mins(5)), metric(MEM, 91.0, mins(5))], mins(5))
    await send(w, tok, HOST, [metric(CPU, 20.0, mins(10)), metric(MEM, 40.0, mins(10))], mins(10))
    return tok


async def cpu_alert(w):
    return next(a for a in await alerts(w, "all") if a["condition_key"] == CPU + "|{}")


async def investigation(w, alert_id, **params):
    r = await w["client"].get(f"/api/v1/alerts/{alert_id}/investigation", params=params, headers=bearer())
    return r


def _row(engine, alert_id):
    with engine.connect() as c:
        return c.execute(text("SELECT trigger_value, trigger_threshold, comparison, trigger_observed_at, recovery_value, "
                              "recovery_observed_at, value FROM alerts WHERE id = :i"), {"i": alert_id}).mappings().one()


# ------------------------------------------------------------------------------------------------- trigger & recovery evidence
async def test_trigger_evidence_is_persisted_at_open_and_never_rewritten(world, engine):
    await run_breach_and_recovery(world)
    a = await cpu_alert(world)
    row = _row(engine, uuid.UUID(a["id"]))
    # Opened by the 97% sample: the trigger is that sample, not the later 98% one nor the recovery value.
    assert row["trigger_value"] == 97.0 and row["trigger_threshold"] == 95.0 and row["comparison"] == ">="
    assert row["trigger_observed_at"] == T0 - timedelta(seconds=0.5)
    # Recovery evidence is the judged recovery sample, separate from the trigger.
    assert row["recovery_value"] == 20.0
    assert row["recovery_observed_at"] == mins(10) - timedelta(seconds=0.5)
    assert row["value"] == 20.0  # the existing value column keeps its contract: the value at recovery


async def test_application_role_cannot_rewrite_trigger_evidence(world, app_engine):
    await run_breach_and_recovery(world)
    a = await cpu_alert(world)
    with pytest.raises(DBAPIError, match="permission denied"):
        with app_engine.begin() as c:
            c.execute(text("UPDATE alerts SET trigger_value = 1 WHERE id = :i"), {"i": a["id"]})
    with pytest.raises(DBAPIError, match="permission denied"):
        with app_engine.begin() as c:
            c.execute(text("UPDATE alerts SET trigger_observed_at = now() WHERE id = :i"), {"i": a["id"]})


# ---------------------------------------------------------------------------------------------------- investigation view
async def test_view_returns_trigger_recovery_series_events_and_related_alerts(world):
    await run_breach_and_recovery(world)
    a = await cpu_alert(world)
    r = await investigation(world, a["id"], window_minutes=30)
    assert r.status_code == 200, r.text
    v = r.json()

    assert v["trigger"]["rule"] == "threshold" and v["trigger"]["metric"] == CPU
    assert v["trigger"]["trigger_value"] == 97.0 and v["trigger"]["threshold"] == 95.0 and v["trigger"]["comparison"] == ">="
    assert v["trigger"]["unavailable"] == []

    assert v["recovery"]["recovered"] is True and v["recovery"]["value"] == 20.0 and v["recovery"]["resolution"] == "recovered"
    assert [e["event"] for e in v["events"]] == ["opened", "recovered"]

    before_trigger = next(s for s in v["series"] if s["window"] == "around_trigger")
    pts = [p for p in before_trigger["points"] if p["value"] is not None]
    assert any(p["t"] < v["trigger"]["first_fired_at"] for p in pts), "telemetry before the trigger is present"
    assert any(p["t"] >= v["trigger"]["first_fired_at"] for p in pts), "telemetry after the trigger is present"
    assert before_trigger["metric"] == CPU and before_trigger["labels"] == {}
    assert any(s["window"] == "around_recovery" for s in v["series"])

    # The memory breach opened at the same evaluation: it is the related alert on the same host.
    assert v["related"]["total"] == 1
    assert v["related"]["items"][0]["metric"] == MEM and v["related"]["items"][0]["severity"] == "warning"
    assert "correlation" in v["related"]["note"]

    assert v["host"]["known"] is True and v["host"]["current"]["host_id"] == HOST
    assert v["settings"]["version"] == 0 and v["settings"]["warning_limit"] == 85.0 and v["settings"]["critical_limit"] == 95.0


async def test_window_and_related_pages_are_bounded_and_validated(world):
    await run_breach_and_recovery(world)
    a = await cpu_alert(world)
    assert (await investigation(world, a["id"], window_minutes=500)).status_code == 422
    assert (await investigation(world, a["id"], window_minutes=2)).status_code == 422
    assert (await investigation(world, a["id"], related_limit=1000)).status_code == 422
    page = (await investigation(world, a["id"], related_limit=1, related_offset=0)).json()["related"]
    assert page["limit"] == 1 and len(page["items"]) <= 1 and page["total"] == 1


async def test_host_offline_alert_has_no_metric_series_and_says_so(world):
    tok = await provision(world)
    await send(world, tok, HOST, [metric(CPU, 22.0, mins(-20))], mins(-20))
    sweep(world, mins(-20) + timedelta(seconds=400))  # silent past the offline window
    off = next(a for a in await alerts(world, "all") if a["rule"] == "host_offline")
    v = (await investigation(world, off["id"])).json()
    assert v["trigger"]["rule"] == "host_offline" and v["series"] == []
    assert "no metric series" in v["series_note"]


async def test_missing_trigger_evidence_is_reported_unavailable_not_invented(world, engine):
    await run_breach_and_recovery(world)
    a = await cpu_alert(world)
    # Simulate an alert opened before this evidence existed (owner role, test setup only).
    with engine.begin() as c:
        c.execute(text("UPDATE alerts SET trigger_value = NULL, trigger_threshold = NULL, comparison = NULL, "
                       "trigger_observed_at = NULL WHERE id = :i"), {"i": a["id"]})
    v = (await investigation(world, a["id"])).json()
    assert v["trigger"]["trigger_value"] is None and v["trigger"]["trigger_observed_at"] is None
    assert set(v["trigger"]["unavailable"]) == {"trigger_value", "trigger_observed_at"}
    r = (await world["client"].post(f"/api/v1/alerts/{a['id']}/investigation/report", headers=bearer())).json()
    assert any("unavailable" in n for n in r["body"]["not_established"])
    assert not any("97.00" in f["text"] for f in r["body"]["facts"]), "no trigger value is stated when none was recorded"


# -------------------------------------------------------------------------------------------------------- persisted record
async def test_report_is_created_once_persists_and_reproduces_from_its_evidence(world, engine):
    await run_breach_and_recovery(world)
    a = await cpu_alert(world)
    created = await world["client"].post(f"/api/v1/alerts/{a['id']}/investigation/report", headers=bearer())
    assert created.status_code == 201, created.text
    rec = created.json()
    assert rec["verified"] is True and rec["digest_matches"] and rec["rederivation_matches"]

    again = await world["client"].post(f"/api/v1/alerts/{a['id']}/investigation/report", headers=bearer())
    assert again.status_code == 200
    assert again.json()["id"] == rec["id"] and again.json()["evidence_digest"] == rec["evidence_digest"]
    assert again.json()["created_at"] == rec["created_at"]

    got = await world["client"].get(f"/api/v1/alerts/{a['id']}/investigation/report", headers=bearer())
    assert got.status_code == 200 and got.json()["evidence_digest"] == rec["evidence_digest"] and got.json()["verified"] is True

    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM investigation_reports WHERE alert_id = :i"), {"i": a["id"]}).scalar_one() == 1


async def test_report_separates_facts_conclusions_and_unknowns_and_does_not_claim_a_cause(world):
    await run_breach_and_recovery(world)
    a = await cpu_alert(world)
    body = (await world["client"].post(f"/api/v1/alerts/{a['id']}/investigation/report", headers=bearer())).json()["body"]
    assert body["schema_version"] == "investigation.v1"
    assert all({"id", "text", "source"} <= set(f) for f in body["facts"])
    conclusions = {c["id"]: c for c in body["conclusions"]}
    assert any("trigger condition was met" in c["text"] for c in body["conclusions"])
    assert any("Recovery is evidenced by telemetry" in c["text"] for c in body["conclusions"])
    assert any("temporal correlation only" in c["text"] for c in body["conclusions"])
    for c in conclusions.values():  # every conclusion is traceable to recorded facts
        assert c["basis"] and all(b in {f["id"] for f in body["facts"]} for b in c["basis"])
    assert any("Root cause is not established" in n for n in body["not_established"])
    assert not any("root cause" in c["text"].lower() for c in body["conclusions"])


async def test_tampered_record_fails_verification(world, engine):
    await run_breach_and_recovery(world)
    a = await cpu_alert(world)
    await world["client"].post(f"/api/v1/alerts/{a['id']}/investigation/report", headers=bearer())
    with engine.begin() as c:  # owner role only: the application cannot do this
        c.execute(text("UPDATE investigation_reports SET body = jsonb_set(body, '{conclusions,0,text}', '\"Forged\"') WHERE alert_id = :i"),
                  {"i": a["id"]})
    got = (await world["client"].get(f"/api/v1/alerts/{a['id']}/investigation/report", headers=bearer())).json()
    assert got["verified"] is False and got["digest_matches"] is False


async def test_application_role_cannot_update_or_delete_a_report(world, app_engine):
    await run_breach_and_recovery(world)
    a = await cpu_alert(world)
    await world["client"].post(f"/api/v1/alerts/{a['id']}/investigation/report", headers=bearer())
    with pytest.raises(DBAPIError, match="permission denied"):
        with app_engine.begin() as c:
            c.execute(text("UPDATE investigation_reports SET created_by = 'x' WHERE alert_id = :i"), {"i": a["id"]})
    with pytest.raises(DBAPIError, match="permission denied"):
        with app_engine.begin() as c:
            c.execute(text("DELETE FROM investigation_reports WHERE alert_id = :i"), {"i": a["id"]})


# ------------------------------------------------------------------------------------------------------------- isolation
def _insert_foreign_alert(engine) -> str:
    """A second workspace with its own host and an open alert, written directly (owner role)."""
    with engine.begin() as c:
        wid = c.execute(text("INSERT INTO workspaces (slug, name) VALUES ('foreign', 'Foreign') RETURNING id")).scalar_one()
        hpk = c.execute(text("INSERT INTO hosts (workspace_id, host_id, kind, created_by) VALUES (:w, 'foreign-host', 'live', 'test') "
                             "RETURNING id"), {"w": wid}).scalar_one()
        aid = c.execute(text(
            "INSERT INTO alerts (workspace_id, host_pk, host_id, condition_key, rule, metric, labels, severity, state, value, threshold, "
            "trigger_value, trigger_threshold, comparison, trigger_observed_at, last_evaluated_at) "
            "VALUES (:w, :h, 'foreign-host', 'system.cpu.utilization|{}', 'threshold', 'system.cpu.utilization', '{}', 'critical', 'open', "
            "97, 95, 97, 95, '>=', now(), now()) RETURNING id"), {"w": wid, "h": hpk}).scalar_one()
    return str(aid)


async def test_another_workspaces_investigation_and_report_are_not_reachable(world, engine):
    foreign = _insert_foreign_alert(engine)
    for url in (f"/api/v1/alerts/{foreign}/investigation",):
        r = await world["client"].get(url, headers=bearer())
        assert r.status_code == 404 and r.json()["error"]["code"] == "alert_not_found"
    r = await world["client"].post(f"/api/v1/alerts/{foreign}/investigation/report", headers=bearer())
    assert r.status_code == 404
    r = await world["client"].get(f"/api/v1/alerts/{foreign}/investigation/report", headers=bearer())
    assert r.status_code == 404
    listed = await alerts(world, "all")
    assert all(a["id"] != foreign for a in listed)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM investigation_reports WHERE alert_id = :i"), {"i": foreign}).scalar_one() == 0


async def test_investigation_endpoints_require_a_browser_session(world):
    await run_breach_and_recovery(world)
    a = await cpu_alert(world)
    url = f"/api/v1/alerts/{a['id']}/investigation"
    assert (await world["client"].get(url)).status_code == 401
    assert (await world["client"].get(url, headers=bearer(sub=OTHER_USER))).status_code == 403
    assert (await world["client"].post(f"{url}/report", headers=bearer(sub=OTHER_USER))).status_code == 403
    assert (await world["client"].get(f"{url}/report", headers=bearer(sub=OTHER_USER))).status_code == 403
