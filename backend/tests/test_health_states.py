"""Host lifecycle states and configurable thresholds.

Part 1 is pure (no database): the exact transition table from app/monitoring/health.py, boundary by boundary.
Part 2 runs the settings API against the disposable PostgreSQL and proves configuration really changes classification.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.monitoring import health as H
from app.monitoring.settings import DEFAULT_CONFIG, MonitoringConfig, Rule, parse_document
from app.main import create_app
from app.registry.store import RegistryCredentialStore
from app.db import make_session_factory
from tests.clerk_support import bearer, verifier
from tests.conftest import FIXED_NOW, make_client

NOW = FIXED_NOW
CPU, MEM, DISK = "system.cpu.utilization", "system.memory.utilization", "system.disk.utilization"


@dataclass
class S:
    name: str
    value: float
    observed_at: datetime
    labels: dict


def cls(age, samples, source="live", cfg=DEFAULT_CONFIG):
    return H.classify(source, NOW - timedelta(seconds=age), NOW, samples, cfg)


def sample(name=CPU, value=10.0, age=0, labels=None):
    return S(name, value, NOW - timedelta(seconds=age), labels or {})


# ------------------------------------------------------------------------------------------------ transition table (pure)
@pytest.mark.parametrize("age,expected", [
    (0, "healthy"), (90, "healthy"),            # inclusive on the fresh side
    (90.001, "stale"), (300, "stale"),
    (300.001, "offline"), (86400, "offline"),
])
def test_freshness_boundaries_with_default_limits(age, expected):
    assert cls(age, [sample(age=age)]).status == expected


def test_stale_and_offline_hosts_are_not_judged_on_their_last_values():
    crit = [sample(CPU, 99.0, age=120)]
    assert cls(120, crit).status == "stale" and cls(120, crit).reasons == []        # last value was critical; still not evaluated
    assert cls(400, crit).status == "offline" and cls(400, crit).reasons == []


@pytest.mark.parametrize("value,expected", [(84.99, "healthy"), (85.0, "warning"), (94.99, "warning"), (95.0, "critical"), (100.0, "critical")])
def test_threshold_boundaries_are_inclusive_at_the_limit(value, expected):
    assert cls(5, [sample(CPU, value, age=5)]).status == expected


def test_disk_uses_its_own_thresholds_and_the_worst_series_wins():
    h = cls(5, [sample(DISK, 89.0, 5, {"mount": "/"}), sample(DISK, 90.0, 5, {"mount": "/data"}), sample(CPU, 10.0, 5)])
    assert h.status == "warning" and [(r.labels, r.level) for r in h.reasons] == [({"mount": "/data"}, "warning")]


def test_no_evaluable_metric_is_unknown_never_healthy_and_never_zero():
    assert cls(5, []).status == "unknown"
    assert cls(5, [sample("app.queue.depth", 5.0, 5)]).status == "unknown"            # only non-health metrics
    old = [sample(CPU, 1.0, age=400)]                                                  # sample far older than the host's last envelope
    assert H.classify("live", NOW - timedelta(seconds=5), NOW, old).status == "unknown"


def test_a_stale_sample_is_skipped_not_treated_as_zero_while_a_fresh_one_still_counts():
    samples = [sample(CPU, 99.0, age=400), sample(MEM, 20.0, age=5)]
    h = H.classify("live", NOW - timedelta(seconds=5), NOW, samples)
    assert h.status == "healthy" and h.reasons == []                                   # cpu ignored (too old), memory evaluated


def test_replay_hosts_are_never_stale_or_offline_by_the_clock():
    assert cls(10 ** 6, [sample(CPU, 99.0, age=10 ** 6)], source="replay").status == "critical"
    assert H.reporting_status("replay", NOW - timedelta(days=9), NOW) == "replay"


def test_reporting_status_matches_health_freshness():
    for age, want in ((0, "reporting"), (90, "reporting"), (91, "stale"), (300, "stale"), (301, "silent")):
        assert H.reporting_status("live", NOW - timedelta(seconds=age), NOW) == want


def test_category_groups_severities_into_degraded():
    assert {s: H.Health(s).category for s in ("healthy", "warning", "critical", "stale", "offline", "unknown")} == {
        "healthy": "healthy", "warning": "degraded", "critical": "degraded", "stale": "stale", "offline": "offline", "unknown": "unknown"}


def test_condition_key_is_stable_and_ignores_value_and_level():
    a = H.Reason(DISK, {"mount": "/", "x": "1"}, 91.0, 90.0, "warning")
    b = H.Reason(DISK, {"x": "1", "mount": "/"}, 99.0, 95.0, "critical")
    assert a.key == b.key                                       # same condition escalating must keep one identity (M2 dedup)
    assert a.key != H.Reason(DISK, {"mount": "/data"}, 91.0, 90.0, "warning").key


def test_custom_limits_change_the_classification():
    cfg = MonitoringConfig(timedelta(seconds=20), timedelta(seconds=40), {**DEFAULT_CONFIG.rules, CPU: Rule(50.0, 60.0)})
    assert cls(5, [sample(CPU, 55.0, age=5)], cfg=cfg).status == "warning"
    assert cls(21, [sample(CPU, 1.0, age=21)], cfg=cfg).status == "stale"
    assert cls(41, [sample(CPU, 1.0, age=41)], cfg=cfg).status == "offline"


# ------------------------------------------------------------------------------------------------ document validation (pure)
GOOD = DEFAULT_CONFIG.to_document()


def doc(**edits):
    d = copy.deepcopy(GOOD)
    for path, value in edits.items():
        node = d
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
    return d


@pytest.mark.parametrize("bad", [
    doc(freshness__stale_after_seconds=5), doc(freshness__stale_after_seconds=9999), doc(freshness__stale_after_seconds=90.5),
    doc(freshness__offline_after_seconds=90), doc(freshness__offline_after_seconds=100000),
    doc(freshness__stale_after_seconds=True),
    {**GOOD, "rules": {**GOOD["rules"], CPU: {"warning": 95, "critical": 85}}},
    {**GOOD, "rules": {**GOOD["rules"], CPU: {"warning": 0, "critical": 85}}},
    {**GOOD, "rules": {**GOOD["rules"], CPU: {"warning": 85, "critical": 101}}},
    {**GOOD, "rules": {**GOOD["rules"], CPU: {"warning": "85", "critical": 95}}},
    {**GOOD, "rules": {**GOOD["rules"], CPU: {"warning": 85, "critical": 95, "extra": 1}}},
    {**GOOD, "rules": {k: v for k, v in GOOD["rules"].items() if k != DISK}},
    {**GOOD, "rules": {**GOOD["rules"], "system.network.rx_bytes": {"warning": 1, "critical": 2}}},
    {**GOOD, "extra": 1}, {"rules": GOOD["rules"]}, [], None, "x",
])
def test_invalid_documents_are_rejected(bad):
    from app.errors import ApiError
    with pytest.raises(ApiError) as e:
        parse_document(bad)
    assert e.value.status == 422


def test_valid_document_round_trips():
    assert parse_document(GOOD).to_document() == GOOD


# ------------------------------------------------------------------------------------------------ settings API (database)
@pytest.fixture
def api(app_engine):
    sf = make_session_factory(app_engine)
    http = make_client(create_app(app_engine, RegistryCredentialStore(sf), clock=lambda: NOW, verifier=verifier()))
    http.headers.update(bearer())
    return http


@pytest.fixture(autouse=False)
def clean_settings(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE monitoring_settings, audit_events RESTART IDENTITY"))
    yield


async def seed_host(app_engine, host_id, age, cpu):
    import uuid
    from app.ingest.fingerprint import content_hash
    from app.ingest.service import store_envelope
    ts = (NOW - timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    env = {"schema_version": "1.0", "envelope_id": str(uuid.uuid4()), "agent_id": "a", "host_id": host_id, "hostname": host_id,
           "timestamp": ts, "metrics": [{"name": CPU, "kind": "gauge", "value": cpu, "unit": "percent", "timestamp": ts}]}
    store_envelope(make_session_factory(app_engine), env, content_hash(env))


@pytest.mark.anyio
async def test_defaults_are_served_when_nothing_is_stored(api, clean_settings):
    body = (await api.get("/api/v1/settings/monitoring")).json()
    assert body["version"] == 0 and body["config"] == GOOD == body["defaults"] and body["updated_by"] is None


@pytest.mark.anyio
async def test_settings_require_an_authorised_session(app_engine, clean_settings):
    plain = make_client(create_app(app_engine, RegistryCredentialStore(make_session_factory(app_engine)), clock=lambda: NOW, verifier=verifier()))
    plain.headers.pop("Authorization", None)
    assert (await plain.get("/api/v1/settings/monitoring")).status_code == 401
    assert (await plain.put("/api/v1/settings/monitoring", json={"config": GOOD, "expected_version": 0})).status_code == 401


@pytest.mark.anyio
async def test_changing_thresholds_changes_health_and_is_versioned_and_audited(api, app_engine, engine, clean_settings):
    await seed_host(app_engine, "h1", age=10, cpu=60.0)
    assert (await api.get("/api/v1/hosts/h1")).json()["health"]["status"] == "healthy"
    new = {**GOOD, "rules": {**GOOD["rules"], CPU: {"warning": 50.0, "critical": 70.0}}}
    r = await api.put("/api/v1/settings/monitoring", json={"config": new, "expected_version": 0})
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["updated_by"] == "user_allowed_1"
    h = (await api.get("/api/v1/hosts/h1")).json()
    assert h["health"]["status"] == "warning" and h["health"]["category"] == "degraded"
    assert h["health"]["reasons"][0]["threshold"] == 50.0 and h["health"]["reasons"][0]["key"].startswith(CPU)
    listing = (await api.get("/api/v1/hosts")).json()["thresholds"]
    assert listing["rules"][CPU] == {"warning": 50.0, "critical": 70.0} and listing["host_stale_after_seconds"] == 90
    with engine.connect() as c:
        ev = c.execute(text("SELECT actor, action FROM audit_events")).all()
    assert [(e.actor, e.action) for e in ev] == [("user_allowed_1", "monitoring_settings.updated")]


@pytest.mark.anyio
async def test_freshness_limits_are_configurable_end_to_end(api, app_engine, clean_settings):
    await seed_host(app_engine, "h2", age=40, cpu=10.0)
    assert (await api.get("/api/v1/hosts/h2")).json()["health"]["status"] == "healthy"             # 40 s <= 90 s default
    tight = {**GOOD, "freshness": {"stale_after_seconds": 30, "offline_after_seconds": 60}}
    await api.put("/api/v1/settings/monitoring", json={"config": tight, "expected_version": 0})
    body = (await api.get("/api/v1/hosts/h2")).json()
    assert body["health"]["status"] == "stale" and body["reporting_status"] == "stale"            # 30 < 40 <= 60
    await seed_host(app_engine, "h3", age=70, cpu=10.0)
    assert (await api.get("/api/v1/hosts/h3")).json()["health"]["status"] == "offline"


@pytest.mark.anyio
async def test_concurrent_edit_is_a_409_and_invalid_input_changes_nothing(api, clean_settings):
    ok = await api.put("/api/v1/settings/monitoring", json={"config": GOOD, "expected_version": 0})
    assert ok.json()["version"] == 1
    stale = await api.put("/api/v1/settings/monitoring", json={"config": GOOD, "expected_version": 0})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"
    bad = await api.put("/api/v1/settings/monitoring", json={"config": doc(freshness__offline_after_seconds=10), "expected_version": 1})
    assert bad.status_code == 422
    assert (await api.get("/api/v1/settings/monitoring")).json()["version"] == 1
    assert (await api.put("/api/v1/settings/monitoring", json={"config": GOOD, "expected_version": 1, "workspace_id": "x"})).status_code == 422


@pytest.mark.anyio
async def test_registry_reports_stale_and_awaiting_states(api, app_engine, engine, clean_settings):
    with engine.begin() as c:
        ws = c.execute(text("SELECT id FROM workspaces WHERE slug='default'")).scalar_one()
        c.execute(text("TRUNCATE agent_credentials, hosts CASCADE"))
        for h in ("h-new", "h-stale", "h-ok"):
            c.execute(text("INSERT INTO hosts (workspace_id, host_id, kind, created_by) VALUES (:w, :h, 'live', 't')"), {"w": ws, "h": h})
    await seed_host(app_engine, "h-stale", age=120, cpu=10.0)
    await seed_host(app_engine, "h-ok", age=5, cpu=10.0)
    states = {h["host_id"]: h["telemetry_state"] for h in (await api.get("/api/v1/registry/hosts")).json()}
    assert states == {"h-new": "awaiting_first_telemetry", "h-stale": "stale", "h-ok": "reporting"}


def test_settings_table_is_protected(app_engine, engine):
    from sqlalchemy.exc import ProgrammingError
    for sql in ("DELETE FROM monitoring_settings", "UPDATE monitoring_settings SET workspace_id = gen_random_uuid()"):
        with pytest.raises(ProgrammingError):
            with app_engine.begin() as c:
                c.execute(text(sql))
    with engine.connect() as c:
        for role in ("anon", "authenticated", "service_role"):
            assert not c.execute(text("SELECT has_table_privilege(:r, 'monitoring_settings', 'SELECT,INSERT,UPDATE,DELETE')"), {"r": role}).scalar_one()
