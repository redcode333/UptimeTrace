"""Application traces as investigation evidence: workspace isolation, trace detail access, and graceful degradation.

Real PostgreSQL, the real ingestion and the real investigation view. Only Jaeger is replaced: a fake transport answers its query
API with trace documents the test builds, so each case is explicit about which service emitted which span.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text

from app.db import make_session_factory
from app.main import create_app
from app.registry.store import RegistryCredentialStore
from app.traces import summary as S
from app.traces.jaeger import JaegerClient
from tests.clerk_support import bearer, verifier
from tests.test_alerts import CPU, HOST, MEM, T0, Clock, alerts, metric, provision, send, world  # noqa: F401 (fixtures)
from tests.test_investigation import clean_investigation, cpu_alert, investigation, run_breach_and_recovery  # noqa: F401

pytestmark = pytest.mark.anyio

MINE_ID = "11111111-1111-4111-8111-111111111111"       # a service of the default workspace
THEIRS_ID = "22222222-2222-4222-8222-222222222222"     # a service of another workspace with the SAME name
START_US = int((T0 - timedelta(minutes=5)).timestamp() * 1_000_000)


def make_trace(trace_id: str, procs: dict[str, tuple[str, str]], spans: list[tuple]) -> dict:
    """procs: pid -> (service.name, uptimetrace.service.id). spans: (span_id, parent_id|None, pid, operation, start_us, dur_us, error)."""
    processes = {pid: {"serviceName": name, "tags": [{"key": S.SERVICE_ID_TAG, "type": "string", "value": sid}]}
                 for pid, (name, sid) in procs.items()}
    out = []
    for span_id, parent, pid, op, start, dur, error in spans:
        refs = [{"refType": "CHILD_OF", "traceID": trace_id, "spanID": parent}] if parent else []
        tags = [{"key": "error", "type": "bool", "value": True}] if error else []
        out.append({"traceID": trace_id, "spanID": span_id, "operationName": op, "references": refs, "startTime": start,
                    "duration": dur, "tags": tags, "processID": pid, "warnings": None})
    return {"traceID": trace_id, "spans": out, "processes": processes, "warnings": None}


MINE = make_trace("a" * 32, {"p1": ("checkout", MINE_ID), "p2": ("inventory", MINE_ID)}, [
    ("s1", None, "p1", "GET /checkout", START_US + 1_000, 2_400_000, False),
    ("s2", "s1", "p2", "inventory.query", START_US + 2_000, 2_200_000, True),
])
THEIRS = make_trace("b" * 32, {"p1": ("checkout", THEIRS_ID)}, [
    ("t1", None, "p1", "GET /checkout", START_US + 1_000, 90_000, False),
])


def fake_jaeger(traces: list[dict], *, down: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if down:
            raise httpx.ConnectError("refused", request=request)
        path = request.url.path
        if path == "/api/traces":
            # Like Jaeger: service and process tags filter FIRST, then the limit applies.
            name = request.url.params.get("service")
            limit = int(request.url.params.get("limit", "20"))
            wanted = json.loads(request.url.params.get("tags", "{}"))
            def carries(process: dict, key: str, value: str) -> bool:
                return any(tag.get("key") == key and tag.get("value") == value for tag in process.get("tags", []))
            data = []
            for t in traces:
                procs = t["processes"].values()
                if not any(p["serviceName"] == name for p in procs):
                    continue
                if wanted and not any(all(carries(p, k, v) for k, v in wanted.items()) for p in procs):
                    continue
                data.append(t)
            data = data[:limit]
            return httpx.Response(200, json={"data": data, "errors": None})
        if path.startswith("/api/traces/"):
            tid = path.rsplit("/", 1)[1]
            hit = [t for t in traces if t["traceID"] == tid]
            if hit:
                return httpx.Response(200, json={"data": hit, "errors": None})
            return httpx.Response(404, json={"data": None, "errors": [{"code": 404, "msg": "trace not found"}]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def remove_seeded_services(engine):
    """Service rows reference workspaces, which the investigation fixtures delete at the start of each test. Remove ours after."""
    yield
    with engine.begin() as c:
        c.execute(text("DELETE FROM service_hosts"))
        c.execute(text("DELETE FROM services"))
        c.execute(text("DELETE FROM workspaces WHERE slug = 'other-tenant'"))


def seed_services(engine, host_id: str) -> None:
    """Default workspace: 'checkout' is assigned to HOST. Another workspace also has a 'checkout' that is NOT assigned to HOST."""
    with engine.begin() as c:
        c.execute(text("DELETE FROM workspaces WHERE slug = 'other-tenant'"))
        c.execute(text("DELETE FROM service_hosts"))
        c.execute(text("DELETE FROM services"))
        ws = c.execute(text("SELECT id FROM workspaces WHERE slug = 'default'")).scalar_one()
        other = c.execute(text("INSERT INTO workspaces (slug, name) VALUES ('other-tenant', 'Other') RETURNING id")).scalar_one()
        host_pk = c.execute(text("SELECT id FROM hosts WHERE host_id = :h"), {"h": host_id}).scalar_one()
        mine = c.execute(text("INSERT INTO services (id, workspace_id, name, created_by) VALUES (:i, :w, 'checkout', 'test') RETURNING id"),
                         {"i": MINE_ID, "w": ws}).scalar_one()
        c.execute(text("INSERT INTO services (id, workspace_id, name, created_by) VALUES (:i, :w, 'checkout', 'test')"),
                  {"i": THEIRS_ID, "w": other})
        c.execute(text("INSERT INTO service_hosts (workspace_id, service_id, host_pk, created_by) VALUES (:w, :s, :h, 'test')"),
                  {"w": ws, "s": mine, "h": host_pk})


def make_app(app_engine, trace_client):
    clock = Clock(T0)
    store = RegistryCredentialStore(make_session_factory(app_engine))
    app = create_app(app_engine, store, clock=clock, verifier=verifier(), trace_client=trace_client)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")
    return {"client": client, "clock": clock, "sf": make_session_factory(app_engine), "engine": app_engine}


def jaeger_client(transport) -> JaegerClient:
    return JaegerClient("http://jaeger.internal:16686", transport=transport)


async def traced_alert(w, engine=None):
    """Breach and recovery for HOST. With an engine, HOST's registered service is seeded after the host exists."""
    await run_breach_and_recovery(w)
    if engine is not None:
        seed_services(engine, HOST)
    return await cpu_alert(w)


# ------------------------------------------------------------------------------------------------ pure trace handling
def test_summary_reports_root_duration_errors_and_slowest_span():
    s = S.summarize(MINE)
    assert s["root"] == {"service": "checkout", "operation": "GET /checkout"}
    assert s["services"] == ["checkout", "inventory"]
    assert s["span_count"] == 2 and s["error_count"] == 1
    assert s["duration_ms"] == 2400.0
    assert s["slowest"] == {"service": "inventory", "operation": "inventory.query", "duration_ms": 2200.0}
    assert s["slow_span_count"] == 2          # both spans are over the 1 s threshold


def test_ownership_is_by_the_registered_service_identity_not_the_name():
    assert S.belongs_to(MINE, {MINE_ID})
    assert not S.belongs_to(THEIRS, {MINE_ID})          # same service.name, different registered identity
    assert not S.belongs_to(MINE, set())


def test_a_trace_without_spans_is_refused_not_summarised():
    with pytest.raises(ValueError):
        S.summarize({"traceID": "c" * 32, "spans": [], "processes": {}})


def test_error_is_recognised_from_the_otel_status_as_well_as_the_error_tag():
    span = {"tags": [{"key": "otel.status_code", "value": "ERROR"}]}
    assert S.is_error(span)
    assert not S.is_error({"tags": [{"key": "otel.status_code", "value": "OK"}]})


# ------------------------------------------------------------------------------------------------ investigation evidence
@pytest.fixture
def seeded(app_engine, engine):
    return app_engine, engine


async def test_investigation_shows_only_this_workspaces_traces(seeded):
    app_engine, engine = seeded
    w = make_app(app_engine, jaeger_client(fake_jaeger([MINE, THEIRS])))
    alert = await traced_alert(w, engine)
    r = await investigation(w, alert["id"], window_minutes=60)
    assert r.status_code == 200, r.text
    tr = r.json()["traces"]
    assert tr["available"] is True and tr["reason"] is None
    assert [i["trace_id"] for i in tr["items"]] == ["a" * 32]
    assert tr["items"][0]["error_count"] == 1
    assert tr["services"] == ["checkout"]


async def test_trace_detail_is_workspace_scoped_and_shows_spans(seeded):
    app_engine, engine = seeded
    w = make_app(app_engine, jaeger_client(fake_jaeger([MINE, THEIRS])))
    await traced_alert(w, engine)
    mine = await w["client"].get(f"/api/v1/traces/{'a' * 32}", headers=bearer())
    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert body["summary"]["trace_id"] == "a" * 32
    assert [s["operation"] for s in body["spans"]] == ["GET /checkout", "inventory.query"]
    assert body["spans"][1]["error"] is True and body["spans"][1]["parent_span_id"] == "s1"
    theirs = await w["client"].get(f"/api/v1/traces/{'b' * 32}", headers=bearer())
    assert theirs.status_code == 404
    missing = await w["client"].get(f"/api/v1/traces/{'d' * 32}", headers=bearer())
    assert missing.status_code == 404                  # identical to "another workspace": existence is not revealed


async def test_malformed_trace_ids_never_reach_the_backend(seeded):
    app_engine, _ = seeded
    seen = []
    def spy(request):
        seen.append(request.url.path)
        return httpx.Response(404)
    w = make_app(app_engine, jaeger_client(httpx.MockTransport(spy)))
    for bad in ("xyz", "A" * 32, "../../api/services", "g" * 32):
        r = await w["client"].get(f"/api/v1/traces/{bad}", headers=bearer())
        assert r.status_code in (404, 422), (bad, r.status_code)
    assert seen == [], "only well-formed trace ids may be sent to the trace backend"


async def test_investigation_still_works_when_the_trace_backend_is_down(seeded):
    app_engine, engine = seeded
    w = make_app(app_engine, jaeger_client(fake_jaeger([MINE], down=True)))
    alert = await traced_alert(w, engine)
    r = await investigation(w, alert["id"], window_minutes=60)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["traces"] == {**body["traces"], "available": False, "reason": "unavailable", "items": []}
    assert body["alert"]["id"] == alert["id"] and body["trigger"]["metric"] == CPU
    detail = await w["client"].get(f"/api/v1/traces/{'a' * 32}", headers=bearer())
    assert detail.status_code == 503


async def test_investigation_reports_not_configured_without_a_trace_backend(seeded):
    app_engine, _ = seeded
    w = make_app(app_engine, None)
    alert = await traced_alert(w)
    r = await investigation(w, alert["id"], window_minutes=60)
    assert r.status_code == 200
    assert r.json()["traces"]["reason"] == "not_configured" and r.json()["traces"]["available"] is False


async def test_host_without_a_registered_service_says_so(seeded):
    app_engine, _ = seeded
    w = make_app(app_engine, jaeger_client(fake_jaeger([MINE])))
    alert = await traced_alert(w)                     # no seed_services: the host has no service assignment
    r = await investigation(w, alert["id"], window_minutes=60)
    assert r.json()["traces"]["reason"] == "no_registered_services"
    assert r.json()["traces"]["items"] == []


async def test_saved_investigation_records_do_not_depend_on_live_traces(seeded):
    app_engine, engine = seeded
    w = make_app(app_engine, jaeger_client(fake_jaeger([MINE])))
    alert = await traced_alert(w, engine)
    created = await w["client"].post(f"/api/v1/alerts/{alert['id']}/investigation/report", headers=bearer())
    assert created.status_code in (200, 201), created.text
    evidence = created.json()["body"]["evidence"]
    assert "traces" not in evidence, "live trace evidence must not enter the report snapshot"
    again = await w["client"].get(f"/api/v1/alerts/{alert['id']}/investigation/report", headers=bearer())
    assert "traces" not in again.json()["body"]["evidence"]


# ------------------------------------------------------------------------------------------------ live Jaeger (opt-in)
LIVE_URL = __import__("os").environ.get("UPTIMETRACE_LIVE_JAEGER_URL")
LIVE_FRONT = "aaaaaaaa-0000-4000-8000-00000000f001"     # must match UPTIMETRACE_SERVICE_ID of the demo frontend-api
LIVE_INV = "aaaaaaaa-0000-4000-8000-00000000f002"       # must match UPTIMETRACE_SERVICE_ID of the demo inventory-api


@pytest.mark.skipif(not (LIVE_URL and os.environ.get("UPTIMETRACE_LIVE_DEMO")),
                    reason="opt-in: needs the demo services running with credentials issued AFTER the test database was reset; set UPTIMETRACE_LIVE_JAEGER_URL and UPTIMETRACE_LIVE_DEMO=1 (see demo/tracing)")
async def test_live_jaeger_end_to_end_investigation(app_engine, engine):
    """Real clock, real Jaeger, real demo services: a breach, a checkout through two registered services, then the investigation."""
    from datetime import UTC, datetime, timedelta as td
    import time
    now = datetime.now(UTC).replace(microsecond=0)
    clock = Clock(now)
    store = RegistryCredentialStore(make_session_factory(app_engine))
    client = JaegerClient(LIVE_URL)
    app = create_app(app_engine, store, clock=clock, verifier=verifier(), trace_client=client)
    w = {"client": httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"),
         "clock": clock}
    tok = await provision(w)
    # A fresh sample: older than the 90 s freshness limit it is not judged, so no alert would open.
    sampled = now - td(seconds=20)
    await send(w, tok, HOST, [metric(CPU, 97.0, sampled)], sampled)
    with engine.begin() as c:
        ws = c.execute(text("SELECT id FROM workspaces WHERE slug = 'default'")).scalar_one()
        host_pk = c.execute(text("SELECT id FROM hosts WHERE host_id = :h"), {"h": HOST}).scalar_one()
        c.execute(text("DELETE FROM service_hosts"))
        c.execute(text("DELETE FROM services"))
        for sid, name in ((LIVE_FRONT, "frontend-api"), (LIVE_INV, "inventory-api")):
            c.execute(text("INSERT INTO services (id, workspace_id, name, created_by) VALUES (:i, :w, :n, 'test')"), {"i": sid, "w": ws, "n": name})
            c.execute(text("INSERT INTO service_hosts (workspace_id, service_id, host_pk, created_by) VALUES (:w, :s, :h, 'test')"),
                      {"w": ws, "s": sid, "h": host_pk})
    listed = await alerts(w, "all")
    matching = [a for a in listed if a["condition_key"] == CPU + "|{}"]
    assert matching, f"the CPU breach did not open an alert: {listed}"
    alert = matching[0]
    front = "http://127.0.0.1:8001"
    async with httpx.AsyncClient(timeout=20) as demo:
        for item in ("ok-live", "slow-live", "broken-live"):
            await demo.get(f"{front}/checkout/{item}")
    # The demo exports in batches and retries with backoff, and Jaeger indexes asynchronously: poll until the traces appear.
    deadline = time.time() + 60
    mine: list = []
    while time.time() < deadline and not mine:
        time.sleep(3)
        r = await investigation(w, alert["id"], window_minutes=30)
        assert r.status_code == 200, r.text
        tr = r.json()["traces"]
        assert tr["available"] is True, tr
        mine = [t for t in tr["items"] if set(t["services"]) >= {"frontend-api", "inventory-api"}]
    assert mine, f"no distributed trace in the window after 60 s: {tr}"
    # The broken checkout and the slow checkout are separate traces: check across them, not within one.
    assert any(t["error_count"] >= 1 for t in mine), mine
    assert any(t["slow_span_count"] >= 1 for t in mine), mine
    top = mine[0]
    detail = await w["client"].get(f"/api/v1/traces/{top['trace_id']}", headers=bearer())
    assert detail.status_code == 200, detail.text
    names = {s["service"] for s in detail.json()["spans"]}
    assert names >= {"frontend-api", "inventory-api"}


async def test_other_workspaces_traces_cannot_crowd_out_this_workspaces_traces(seeded):
    """The search returns at most 50 traces per service name. Traces of another workspace's service with the SAME name must
    not take those 50 places: the identity filter runs in Jaeger before the limit."""
    app_engine, engine = seeded
    crowd = [make_trace(f"{i:032x}", {"p1": ("checkout", THEIRS_ID)}, [
        (f"c{i}", None, "p1", "GET /checkout", START_US + 1_000 + i, 1_000, False)]) for i in range(1, 80)]
    w = make_app(app_engine, jaeger_client(fake_jaeger(crowd + [MINE])))
    alert = await traced_alert(w, engine)
    r = await investigation(w, alert["id"], window_minutes=60)
    tr = r.json()["traces"]
    assert [i["trace_id"] for i in tr["items"]] == ["a" * 32], tr
    assert all(THEIRS_ID not in json.dumps(i) for i in tr["items"])
