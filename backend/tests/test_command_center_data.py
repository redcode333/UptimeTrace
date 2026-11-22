"""Command Center data contracts: alert histogram aggregation, host rankings and scoped LIVE counts.

Real PostgreSQL (the throwaway test database). Alerts are written with the owner role, as the investigation tests do, because the
browser API has no alert-creation route. Hosts are seeded through the real ingestion path.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.db import make_session_factory
from app.ingest.fingerprint import content_hash
from app.ingest.service import store_envelope
from app.main import create_app
from tests.clerk_support import bearer, verifier
from tests.conftest import FIXED_NOW, TEST_CREDENTIALS, make_client

pytestmark = pytest.mark.anyio

NOW = FIXED_NOW
CPU = "system.cpu.utilization"
MEM = "system.memory.utilization"
DISK = "system.disk.utilization"


def utc(seconds_from_now: float) -> str:
    return (NOW + timedelta(seconds=seconds_from_now)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@pytest.fixture(autouse=True)
def clean(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE alert_events, alerts, monitoring_settings, telemetry_envelopes, agent_credentials, service_hosts, services, "
                       "clusters, regions, environments, hosts, audit_events RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def client(app_engine):
    http = make_client(create_app(app_engine, TEST_CREDENTIALS, clock=lambda: FIXED_NOW, verifier=verifier()))
    http.headers.update(bearer())
    return http


@pytest.fixture
def anon_client(app_engine):
    return make_client(create_app(app_engine, TEST_CREDENTIALS, clock=lambda: FIXED_NOW, verifier=verifier()))


@pytest.fixture
def seed(app_engine):
    factory = make_session_factory(app_engine)

    def _seed(host_id: str, sent_ago: float, metrics: list[dict]) -> None:
        envelope = {
            "schema_version": "1.0", "envelope_id": str(uuid.uuid4()), "agent_id": f"agent-{host_id}", "host_id": host_id,
            "hostname": host_id, "timestamp": utc(-sent_ago), "metrics": metrics,
        }
        store_envelope(factory, envelope, content_hash(envelope))

    return _seed


def gauge(name: str, value: float, ago: float = 5) -> dict:
    return {"name": name, "kind": "gauge", "value": value, "unit": "percent", "timestamp": utc(-ago)}


def _default_workspace(c) -> str:
    return str(c.execute(text("SELECT id FROM workspaces WHERE slug = 'default'")).scalar_one())


def _insert_alerts(engine, rows: list[tuple[str, str, float, str]], workspace: str | None = None) -> None:
    """rows: (host_id, severity, seconds_from_now, state). Creates each host once, in the given workspace (default if None)."""
    with engine.begin() as c:
        wid = workspace or _default_workspace(c)
        hosts: dict[str, str] = {}
        for host_id, severity, offset, state in rows:
            if host_id not in hosts:
                hosts[host_id] = str(c.execute(text(
                    "INSERT INTO hosts (workspace_id, host_id, kind, created_by) VALUES (:w, :h, 'live', 'test') "
                    "ON CONFLICT (host_id) DO UPDATE SET host_id = EXCLUDED.host_id RETURNING id"), {"w": wid, "h": host_id}).scalar_one())
            at = NOW + timedelta(seconds=offset)
            acked = at if state in ("acknowledged", "resolved") else None
            resolved = at if state == "resolved" else None
            c.execute(text(
                "INSERT INTO alerts (workspace_id, host_pk, host_id, condition_key, rule, metric, labels, severity, state, value, "
                "threshold, trigger_value, trigger_threshold, comparison, trigger_observed_at, last_evaluated_at, opened_at, "
                "acknowledged_at, acknowledged_by, resolved_at, resolved_by, resolution) "
                "VALUES (:w, :hp, :h, :k, 'threshold', :m, '{}', :sev, :st, 97, 95, 97, 95, '>=', :at, :at, :at, "
                ":ack, :ackby, :res, :resby, :resolution)"),
                {"w": wid, "hp": hosts[host_id], "h": host_id, "k": f"{CPU}|{uuid.uuid4()}", "m": CPU, "sev": severity,
                 "st": state, "at": at, "ack": acked, "ackby": "test" if acked else None, "res": resolved,
                 "resby": "test" if resolved else None, "resolution": "recovered" if resolved else None})


def _histogram(client, start_off: float, end_off: float, bucket: int = 300, source: str | None = None):
    params = {"start": utc(start_off), "end": utc(end_off), "bucket_seconds": bucket}
    if source:
        params["source"] = source
    return client.get("/api/v1/alerts/histogram", params=params)


# ------------------------------------------------------------------------------------------------ alert histogram

async def test_counts_every_alert_in_the_window_even_beyond_the_200_record_list_limit(client, engine):
    rows = [(f"h{i % 9}", "critical" if i % 3 == 0 else "warning", -60 - i, "open") for i in range(250)]
    _insert_alerts(engine, rows)
    r = await _histogram(client, -3600, 0)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 250 and body["complete"] is True
    assert body["critical"] == sum(1 for i in range(250) if i % 3 == 0)
    assert body["warning"] == 250 - body["critical"]
    assert sum(b["critical"] + b["warning"] for b in body["buckets"]) == 250


async def test_start_is_inclusive_and_end_is_exclusive(client, engine):
    _insert_alerts(engine, [("h1", "critical", -3600, "open"), ("h1", "warning", 0, "open"), ("h1", "critical", -3601, "open")])
    body = (await _histogram(client, -3600, 0)).json()
    assert body["total"] == 1 and body["critical"] == 1   # -3600 counted; -3601 before the window; 0 is at the excluded end


async def test_buckets_are_utc_aligned_and_an_alert_on_a_boundary_belongs_to_the_later_bucket(client, engine):
    # NOW is 12:00:00Z; 300 s buckets start on multiples of 5 minutes. An alert at 12:05:00 belongs to the 12:05 bucket.
    _insert_alerts(engine, [("h1", "critical", 0, "open")])
    body = (await _histogram(client, -900, 900, bucket=300)).json()
    for b in body["buckets"]:
        s = b["start"]                      # e.g. 2026-11-11T12:05:00Z: minutes a multiple of five, seconds zero
        assert int(s[14:16]) % 5 == 0 and s[17:19] == "00" and s.endswith("Z"), s
    counted = [b for b in body["buckets"] if b["critical"] + b["warning"] > 0]
    assert len(counted) == 1 and counted[0]["start"].startswith("2026-11-11T12:00:00") and counted[0]["critical"] == 1


async def test_empty_buckets_are_present_with_zero_counts(client, engine):
    _insert_alerts(engine, [("h1", "critical", -3000, "open")])
    body = (await _histogram(client, -3600, 0, bucket=600)).json()
    assert len(body["buckets"]) == 6
    assert sum(1 for b in body["buckets"] if b["critical"] == 0 and b["warning"] == 0) == 5


async def test_severity_is_split_by_its_actual_value(client, engine):
    _insert_alerts(engine, [("h1", "critical", -100, "open"), ("h2", "warning", -100, "resolved"), ("h3", "warning", -200, "open")])
    body = (await _histogram(client, -3600, 0)).json()
    assert (body["critical"], body["warning"], body["total"]) == (1, 2, 3)


async def test_resolved_and_active_alerts_both_count_when_they_opened_in_the_window(client, engine):
    _insert_alerts(engine, [("h1", "critical", -100, "resolved"), ("h1", "critical", -200, "acknowledged")])
    assert (await _histogram(client, -3600, 0)).json()["total"] == 2


async def test_source_scope_uses_the_host_id_rule(client, engine):
    _insert_alerts(engine, [("live-a", "critical", -100, "open"), ("sim01-a", "critical", -100, "open"), ("replay-a", "warning", -100, "open")])
    assert (await _histogram(client, -3600, 0, source="live")).json()["total"] == 1
    assert (await _histogram(client, -3600, 0, source="synthetic")).json()["total"] == 1
    assert (await _histogram(client, -3600, 0, source="replay")).json()["total"] == 1
    assert (await _histogram(client, -3600, 0, source="live,synthetic")).json()["total"] == 2


async def test_another_workspaces_alerts_are_never_counted(client, engine):
    with engine.begin() as c:
        wid = c.execute(text("INSERT INTO workspaces (slug, name) VALUES (:s, 'Other') RETURNING id"), {"s": f"other-{uuid.uuid4().hex[:8]}"}).scalar_one()
    _insert_alerts(engine, [("foreign-h", "critical", -100, "open")], workspace=str(wid))
    _insert_alerts(engine, [("own-h", "critical", -100, "open")])
    assert (await _histogram(client, -3600, 0)).json()["total"] == 1


async def test_an_empty_database_returns_all_buckets_at_zero(client):
    body = (await _histogram(client, -3600, 0, bucket=900)).json()
    assert body["total"] == 0 and len(body["buckets"]) == 4


async def test_invalid_windows_are_refused_not_guessed(client):
    assert (await _histogram(client, 0, 0)).status_code == 422                       # end must follow start
    assert (await _histogram(client, -40 * 86400, 0)).status_code == 422             # longer than the 31-day limit
    assert (await _histogram(client, -86400 * 30, 0, bucket=60)).status_code == 422  # too many buckets
    assert (await _histogram(client, -3600, 0, source="mars")).status_code == 422    # unknown source is never ignored


async def test_a_timestamp_without_an_offset_is_refused(client):
    r = await client.get("/api/v1/alerts/histogram", params={"start": "2026-11-11T11:00:00", "end": "2026-11-11T12:00:00", "bucket_seconds": 300})
    assert r.status_code == 422


async def test_histogram_needs_an_authorised_session(anon_client):
    r = await _histogram(anon_client, -3600, 0)
    assert r.status_code in (401, 403)


# ------------------------------------------------------------------------------------------------ rankings

async def test_ranks_the_true_top_across_more_hosts_than_one_page(client, seed):
    for i in range(12):
        seed(f"host-{i:02d}", 5, [gauge(CPU, float(i * 5))])
    body = (await client.get("/api/v1/hosts/rankings", params={"metric": CPU, "limit": 8})).json()
    assert body["total_in_scope"] == 12 and body["with_value"] == 12 and body["without_value"] == 0
    assert [i["value"] for i in body["items"]] == [55.0, 50.0, 45.0, 40.0, 35.0, 30.0, 25.0, 20.0]
    assert body["items"][0]["host_id"] == "host-11"


async def test_missing_and_stale_values_are_not_ranked_as_zero(client, seed):
    seed("fresh-high", 5, [gauge(CPU, 80.0)])
    seed("fresh-low", 5, [gauge(CPU, 10.0)])
    seed("no-cpu", 5, [gauge(MEM, 40.0)])                 # reports, but has no CPU sample
    seed("stale-cpu", 900, [gauge(CPU, 99.0, ago=900)])    # sample older than the staleness window
    body = (await client.get("/api/v1/hosts/rankings", params={"metric": CPU, "limit": 8})).json()
    ids = [i["host_id"] for i in body["items"]]
    assert ids == ["fresh-high", "fresh-low"]
    assert body["total_in_scope"] == 4 and body["with_value"] == 2 and body["without_value"] == 2


async def test_ranking_is_scoped_by_source(client, seed):
    seed("live-a", 5, [gauge(CPU, 30.0)])
    seed("sim01-a", 5, [gauge(CPU, 90.0)])
    live = (await client.get("/api/v1/hosts/rankings", params={"metric": CPU, "source": "live"})).json()
    assert [i["host_id"] for i in live["items"]] == ["live-a"] and live["total_in_scope"] == 1
    everything = (await client.get("/api/v1/hosts/rankings", params={"metric": CPU})).json()
    assert everything["items"][0]["host_id"] == "sim01-a"


async def test_each_ranked_host_carries_its_observation_time_and_freshness(client, seed):
    seed("live-a", 7, [gauge(CPU, 30.0, ago=7)])
    item = (await client.get("/api/v1/hosts/rankings", params={"metric": CPU})).json()["items"][0]
    assert item["observed_at"].startswith("2026-11-11T11:59:53")
    assert item["last_seen_age_seconds"] == pytest.approx(7, abs=1)
    assert item["reporting_status"] in ("reporting", "stale", "silent")


async def test_an_unknown_metric_is_refused(client):
    assert (await client.get("/api/v1/hosts/rankings", params={"metric": "system.disk.utilization"})).status_code == 422


async def test_empty_fleet_ranks_nothing_and_says_so(client):
    body = (await client.get("/api/v1/hosts/rankings", params={"metric": CPU})).json()
    assert body == {**body, "items": [], "total_in_scope": 0, "with_value": 0, "without_value": 0, "complete": True}


# ------------------------------------------------------------------------------------------------ LIVE scope counts

async def test_live_counts_and_health_come_from_the_live_population_only(client, seed):
    seed("live-a", 5, [gauge(CPU, 96.0)])          # critical under the default rule
    seed("live-b", 5, [gauge(CPU, 10.0)])
    seed("sim01-a", 5, [gauge(CPU, 96.0)])
    body = (await client.get("/api/v1/hosts", params={"source": "live", "page_size": 1})).json()
    assert body["total"] == 2
    assert body["facets"]["health"]["critical"] == 1 and body["facets"]["health"]["healthy"] == 1
    assert body["facets"]["source"]["synthetic"] == 1   # the whole-fleet source facet is unchanged by the source filter


async def _connected(client) -> int:
    body = (await client.get("/api/v1/hosts", params={"page_size": 1})).json()
    return body["facets"]["connected"]


async def test_connected_counts_only_fresh_live_hosts(client, seed):
    seed("live-fresh", 5, [gauge(CPU, 10.0)])            # counted
    seed("live-old", 400, [gauge(CPU, 10.0)])             # older than 300 s: not counted
    seed("live-edge", 300, [gauge(CPU, 10.0)])            # exactly at the boundary: connected, as the classifier says
    seed("sim01-fresh", 5, [gauge(CPU, 10.0)])            # synthetic: never connected
    seed("replay-fresh", 5, [gauge(CPU, 10.0)])           # replay: never connected
    assert await _connected(client) == 2


async def test_connected_is_zero_with_no_telemetry(client):
    assert await _connected(client) == 0


async def test_connected_uses_each_hosts_latest_envelope(client, seed):
    seed("live-recovered", 400, [gauge(CPU, 10.0)])       # an old envelope, then a fresh one: latest is fresh -> connected
    seed("live-recovered", 5, [gauge(CPU, 10.0)])
    seed("live-quiet", 400, [gauge(CPU, 10.0)])           # every envelope is old (latest 350 s) -> not connected
    seed("live-quiet", 350, [gauge(CPU, 10.0)])
    seed("live-late-old", 5, [gauge(CPU, 10.0)])          # fresh latest timestamp, sent first ...
    seed("live-late-old", 400, [gauge(CPU, 10.0)])        # ... an older envelope arriving later does not hide it -> connected
    assert await _connected(client) == 2


# ------------------------------------------------------------------------------------------------ attention and distribution

async def test_attention_order_is_applied_to_the_whole_scope_before_the_page_is_cut(client, seed):
    for i in range(12):
        seed(f"warn-{i:02d}", 5, [gauge(CPU, 88.0)])
    for i in range(3):
        seed(f"crit-{i}", 5, [gauge(CPU, 97.0)])
    body = (await client.get("/api/v1/hosts", params={"health": "warning,critical", "sort": "attention", "page_size": 10})).json()
    assert body["total"] == 15
    ids = [h["host_id"] for h in body["items"]]
    assert ids[:3] == ["crit-0", "crit-1", "crit-2"]
    assert all(h["health"]["status"] == "warning" for h in body["items"][3:])


async def test_distribution_counts_every_in_scope_host_beyond_the_200_list_limit(client, seed):
    for i in range(230):
        seed(f"d-{i:03d}", 5, [gauge(CPU, float(i % 100))])
    body = (await client.get("/api/v1/hosts/distribution", params={"metric": "cpu"})).json()
    assert body["total_in_scope"] == 230 and body["no_data"] == 0 and body["complete"] is True
    assert body["buckets"] == [60, 50, 40, 40, 40]   # values 0..99 twice, then 0..29: the buckets 0-20, 20-40, 40-60, 60-80, 80-100
    assert sum(body["buckets"]) + body["no_data"] == 230


async def test_missing_and_stale_values_are_no_data_never_a_bucket(client, seed):
    seed("hot", 5, [gauge(CPU, 97.0)])
    seed("warm", 5, [gauge(CPU, 88.0)])
    seed("mild", 5, [gauge(CPU, 10.0)])
    seed("memory-only", 5, [gauge(MEM, 40.0)])
    seed("old", 900, [gauge(CPU, 50.0, ago=900)])
    body = (await client.get("/api/v1/hosts/distribution", params={"metric": "cpu"})).json()
    assert body["no_data"] == 2
    assert body["buckets"] == [1, 0, 0, 0, 2]
    assert body["states"] == {"normal": 1, "warning": 1, "critical": 1, "none": 2}
    assert body["max"]["value"] == 97.0 and body["max"]["hostname"] == "hot"


async def test_disk_is_judged_by_its_worst_mount(client, seed):
    seed("disk-host", 5, [
        {"name": DISK, "kind": "gauge", "value": 40.0, "unit": "percent", "labels": {"mount": "/"}, "timestamp": utc(-5)},
        {"name": DISK, "kind": "gauge", "value": 92.0, "unit": "percent", "labels": {"mount": "/data"}, "timestamp": utc(-5)},
    ])
    body = (await client.get("/api/v1/hosts/distribution", params={"metric": "disk_max"})).json()
    assert body["buckets"] == [0, 0, 0, 0, 1] and body["states"]["warning"] == 1 and body["max"]["value"] == 92.0


async def test_distribution_is_scoped_by_source(client, seed):
    seed("live-a", 5, [gauge(CPU, 30.0)])
    seed("sim01-a", 5, [gauge(CPU, 96.0)])
    live = (await client.get("/api/v1/hosts/distribution", params={"metric": "cpu", "source": "live"})).json()
    assert live["total_in_scope"] == 1 and live["max"]["host_id"] == "live-a"
    everything = (await client.get("/api/v1/hosts/distribution", params={"metric": "cpu"})).json()
    assert everything["total_in_scope"] == 2 and everything["max"]["host_id"] == "sim01-a"


async def test_an_empty_fleet_distribution_is_zero_not_missing(client):
    body = (await client.get("/api/v1/hosts/distribution", params={"metric": "memory"})).json()
    assert body["total_in_scope"] == 0 and body["buckets"] == [0, 0, 0, 0, 0] and body["max"] is None and body["complete"] is True


async def test_an_unknown_distribution_metric_is_refused(client):
    assert (await client.get("/api/v1/hosts/distribution", params={"metric": "network"})).status_code == 422


async def test_distribution_needs_an_authorised_session(anon_client):
    assert (await anon_client.get("/api/v1/hosts/distribution", params={"metric": "cpu"})).status_code in (401, 403)


# ------------------------------------------------------------------------------------------------ hosts board: registry placement

async def _place(client, seed, host_id: str, *, cluster: str | None = None, services: tuple[str, ...] = ()):
    seed(host_id, 5, [gauge(CPU, 20.0)])
    assert (await client.post("/api/v1/registry/hosts", json={"host_id": host_id, "kind": "live"})).status_code == 201
    if cluster:
        assert (await client.post(f"/api/v1/infrastructure/clusters/{cluster}/hosts", json={"host_id": host_id})).status_code == 201
    for svc in services:
        assert (await client.post(f"/api/v1/infrastructure/services/{svc}/hosts", json={"host_id": host_id})).status_code == 201


async def _topology(client):
    env = (await client.post("/api/v1/infrastructure/environments", json={"name": "prod"})).json()
    reg = (await client.post("/api/v1/infrastructure/regions", json={"name": "eu-west", "environment_id": env["id"]})).json()
    cl_a = (await client.post("/api/v1/infrastructure/clusters", json={"name": "alpha", "region_id": reg["id"]})).json()
    cl_b = (await client.post("/api/v1/infrastructure/clusters", json={"name": "beta"})).json()
    svc = (await client.post("/api/v1/infrastructure/services", json={"name": "api"})).json()
    return env, reg, cl_a, cl_b, svc


async def test_environment_filter_applies_to_the_whole_fleet_not_one_page(client, seed):
    env, reg, cl_a, cl_b, svc = await _topology(client)
    for i in range(30):
        await _place(client, seed, f"alpha-{i:02d}", cluster=cl_a["id"])
    for i in range(5):
        await _place(client, seed, f"beta-{i}", cluster=cl_b["id"])
    await _place(client, seed, "loose-1")
    body = (await client.get("/api/v1/hosts", params={"environment": env["id"], "page_size": 10})).json()
    assert body["total"] == 30 and len(body["items"]) == 10
    assert all(h["environment"]["name"] == "prod" and h["region"]["name"] == "eu-west" and h["cluster"]["name"] == "alpha" for h in body["items"])
    assert body["facets"]["source"]["live"] == 30          # facets narrow with the group filter too
    unfiltered = (await client.get("/api/v1/hosts", params={"page_size": 200})).json()
    assert unfiltered["total"] == 36


async def test_cluster_and_service_filters_match_their_own_members(client, seed):
    env, reg, cl_a, cl_b, svc = await _topology(client)
    await _place(client, seed, "a-1", cluster=cl_a["id"], services=(svc["id"],))
    await _place(client, seed, "a-2", cluster=cl_a["id"])
    await _place(client, seed, "b-1", cluster=cl_b["id"], services=(svc["id"],))
    by_cluster = (await client.get("/api/v1/hosts", params={"cluster": cl_b["id"]})).json()
    assert [h["host_id"] for h in by_cluster["items"]] == ["b-1"]
    by_service = (await client.get("/api/v1/hosts", params={"service": svc["id"]})).json()
    assert sorted(h["host_id"] for h in by_service["items"]) == ["a-1", "b-1"]
    assert by_service["items"][0]["services"] == [{"id": svc["id"], "name": "api"}]
    both = (await client.get("/api/v1/hosts", params={"cluster": cl_a["id"], "service": svc["id"]})).json()
    assert [h["host_id"] for h in both["items"]] == ["a-1"]


async def test_a_host_with_no_registry_row_has_no_placement_and_is_never_in_a_group(client, seed):
    env, reg, cl_a, cl_b, svc = await _topology(client)
    seed("unregistered", 5, [gauge(CPU, 30.0)])
    body = (await client.get("/api/v1/hosts", params={"page_size": 50})).json()
    row = next(h for h in body["items"] if h["host_id"] == "unregistered")
    assert row["environment"] is None and row["cluster"] is None and row["services"] == []
    filtered = (await client.get("/api/v1/hosts", params={"environment": env["id"]})).json()
    assert filtered["total"] == 0


async def test_group_filters_are_refused_without_a_session(anon_client):
    assert (await anon_client.get("/api/v1/hosts", params={"cluster": "00000000-0000-0000-0000-000000000000"})).status_code in (401, 403)
