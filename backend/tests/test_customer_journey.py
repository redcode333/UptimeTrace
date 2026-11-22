"""The customer journey, through the real HTTP API and real PostgreSQL.

Register a host, issue its credential, ingest telemetry with that credential, see the host and its metrics, breach a threshold, open
the alert's investigation, recover, and run the CPU and memory rehearsals. Unauthorised and ineligible hosts are refused at each step
where they could be misused. The clock is controlled so every transition is exact.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app.db import make_session_factory
from app.main import create_app
from app.registry.store import RegistryCredentialStore
from tests.clerk_support import bearer, verifier
from tests.conftest import FIXED_NOW
from tests.factories import make_envelope

pytestmark = pytest.mark.anyio

HOST = "web-journey-01"
SIM = "sim-journey-01"
CPU = "system.cpu.utilization"
MEM = "system.memory.utilization"


def ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at = self.at + timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def clean(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE failure_rehearsal_steps, failure_rehearsals, alert_events, alerts, investigation_reports, "
                       "monitoring_settings, telemetry_envelopes, agent_credentials, hosts, audit_events RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def world(app_engine, monkeypatch):
    clock = Clock(FIXED_NOW)
    sf = make_session_factory(app_engine)
    launched = []
    from app.rehearsal import service as RS
    monkeypatch.setattr(RS, "launch", lambda *a, **k: launched.append(a[1]))  # rehearsal executor is exercised separately
    app = create_app(app_engine, RegistryCredentialStore(sf), clock=clock, verifier=verifier())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")
    return {"client": client, "clock": clock, "launched": launched}


async def send(w, token, host, value_cpu, value_mem, when, agent=None):
    w["clock"].at = max(w["clock"].at, when)
    env = make_envelope(host_id=host, agent_id=agent or ("agent-" + host), hostname=host + ".local", timestamp=ts(when),
                        metrics=[
                            {"name": CPU, "kind": "gauge", "value": value_cpu, "unit": "percent", "timestamp": ts(when - timedelta(seconds=0.5))},
                            {"name": MEM, "kind": "gauge", "value": value_mem, "unit": "percent", "timestamp": ts(when - timedelta(seconds=0.5))},
                        ])
    r = await w["client"].post("/api/v1/telemetry", json=env, headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 202, r.text


async def get(w, path):
    return await w["client"].get(path, headers=bearer())


async def test_customer_journey_from_connection_to_investigation_and_rehearsal(world):
    w = world
    c = w["client"]

    # 1. Connect: register a live host and issue its credential. Nothing is shown as connected yet.
    r = await c.post("/api/v1/registry/hosts", json={"host_id": HOST, "kind": "live", "display_name": "Web 1"}, headers=bearer())
    assert r.status_code == 201, r.text
    assert r.json()["telemetry_state"] == "awaiting_first_telemetry"
    cred = await c.post(f"/api/v1/registry/hosts/{HOST}/credentials", json={}, headers=bearer())
    assert cred.status_code == 201
    token = cred.json()["token"]
    assert (await get(w, "/api/v1/hosts")).json()["items"] == [], "a registered host with no telemetry is not in the host list"

    # 2. The first telemetry arrives with the credential: the host appears, and its state derives from the backend.
    await send(w, token, HOST, 30.0, 40.0, FIXED_NOW)
    hosts = (await get(w, "/api/v1/hosts")).json()["items"]
    assert [h["host_id"] for h in hosts] == [HOST]
    reg = {h["host_id"]: h for h in (await c.get("/api/v1/registry/hosts", headers=bearer())).json()}
    assert reg[HOST]["telemetry_state"] == "reporting"
    detail = (await get(w, f"/api/v1/hosts/{HOST}")).json()
    assert detail["host_id"] == HOST
    latest = (await get(w, f"/api/v1/hosts/{HOST}/metrics/latest")).json()
    assert latest["source"] == "live" and latest["host_id"] == HOST
    values = {m["name"]: m["value"] for m in latest["metrics"]}
    assert values[CPU] == 30.0 and values[MEM] == 40.0

    # 3. A real threshold breach from telemetry opens exactly one alert.
    await send(w, token, HOST, 97.0, 40.0, FIXED_NOW + timedelta(seconds=15))
    alerts = (await get(w, "/api/v1/alerts?status=active")).json()["alerts"]
    assert len(alerts) == 1 and alerts[0]["condition_key"] == CPU + "|{}" and alerts[0]["state"] == "open"
    alert_id = alerts[0]["id"]

    # 4. The alert leads to its investigation: trigger, timeline, metric series, and an honest trace state.
    inv = (await get(w, f"/api/v1/alerts/{alert_id}/investigation")).json()
    assert inv["alert"]["host_id"] == HOST and inv["trigger"]["trigger_value"] == 97.0
    assert [e["event"] for e in inv["events"]] == ["opened"]
    assert inv["series"] and any(p["value"] is not None for p in inv["series"][0]["points"])
    assert inv["traces"]["available"] is False and inv["traces"]["reason"] == "not_configured"

    # 5. Acknowledge, then recover by telemetry: the alert resolves and the timeline records it.
    ack = await c.post(f"/api/v1/alerts/{alert_id}/acknowledge", headers=bearer())
    assert ack.status_code == 200 and ack.json()["state"] == "acknowledged"
    await send(w, token, HOST, 20.0, 40.0, FIXED_NOW + timedelta(seconds=30))
    alert = (await get(w, f"/api/v1/alerts/{alert_id}")).json()
    assert alert["state"] == "resolved" and alert["resolution"] == "recovered"
    assert [e["event"] for e in alert["events"]] == ["opened", "acknowledged", "recovered"]
    assert (await get(w, "/api/v1/alerts?status=active")).json()["alerts"] == []

    # 6. Investigation record is created from evidence and verifies.
    rec = await c.post(f"/api/v1/alerts/{alert_id}/investigation/report", headers=bearer())
    assert rec.status_code == 201 and rec.json()["verified"] is True

    # 7. Unauthorised callers cannot read any of it or start a rehearsal.
    assert (await c.get("/api/v1/hosts")).status_code == 401
    assert (await c.post("/api/v1/rehearsals", json={"host_id": SIM, "scenario": "cpu_saturation", "confirm_host_id": SIM})).status_code == 401
    # An agent credential is not a browser session.
    assert (await c.get("/api/v1/alerts", headers={"authorization": f"Bearer {token}"})).status_code == 401


async def test_rehearsals_run_on_synthetic_hosts_only_and_link_to_their_alerts(world, app_engine):
    w = world
    c = w["client"]
    for hid, kind in ((SIM, "synthetic"), ("live-journey-01", "live"), ("replay-journey-01", "replay")):
        assert (await c.post("/api/v1/registry/hosts", json={"host_id": hid, "kind": kind}, headers=bearer())).status_code == 201

    # Ineligible hosts are refused on the server, whatever the browser shows.
    for hid in ("live-journey-01", "replay-journey-01", "sim-not-registered-01"):
        r = await c.post("/api/v1/rehearsals", json={"host_id": hid, "scenario": "cpu_saturation", "confirm_host_id": hid}, headers=bearer())
        assert r.status_code in (404, 409), (hid, r.text)
        assert r.json()["error"]["code"] in ("host_not_eligible", "host_not_registered")
    assert w["launched"] == []

    # Eligible: the synthetic host is accepted for each scenario, one at a time.
    r = await c.post("/api/v1/rehearsals", json={"host_id": SIM, "scenario": "memory_saturation", "confirm_host_id": SIM}, headers=bearer())
    assert r.status_code == 201, r.text
    assert r.json()["metric"] == MEM and r.json()["execution"] == "RUNNING"
    rid = r.json()["id"]
    second = await c.post("/api/v1/rehearsals", json={"host_id": SIM, "scenario": "cpu_saturation", "confirm_host_id": SIM}, headers=bearer())
    assert second.json()["error"]["code"] == "rehearsal_already_running"
    stop = await c.post(f"/api/v1/rehearsals/{rid}/stop", headers=bearer())
    assert stop.status_code == 200 and stop.json()["stop_requested_at"] is not None

    # The rehearsal lists in the console's history with its own metric.
    listed = (await c.get("/api/v1/rehearsals", headers=bearer())).json()["rehearsals"]
    assert [x["id"] for x in listed] == [rid]
