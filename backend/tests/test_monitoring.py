"""Read API (P3): hosts, health, latest metrics, time series. Real PostgreSQL, least-privilege `uptimetrace_app` role.

Envelopes are seeded straight through the ingestion store (the read side never trusts the HTTP path to have written them).
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.db import make_session_factory
from app.ingest.fingerprint import content_hash
from app.ingest.service import store_envelope
from app.main import create_app
from tests.clerk_support import bearer, verifier
from tests.conftest import FIXED_NOW, TEST_CREDENTIALS, make_client

pytestmark = pytest.mark.anyio

NOW = FIXED_NOW


def iso(seconds_ago: float) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def gauge(name: str, value: float, ago: float, labels: dict | None = None, unit: str = "percent") -> dict:
    m = {"name": name, "kind": "gauge", "value": value, "unit": unit, "timestamp": iso(ago)}
    if labels:
        m["labels"] = labels
    return m


def counter(name: str, value: int, ago: float, labels: dict | None = None) -> dict:
    m = {"name": name, "kind": "counter", "value": value, "unit": "bytes", "timestamp": iso(ago)}
    m["labels"] = labels or {"interface": "eth0"}
    return m


CPU, MEM, DISK = "system.cpu.utilization", "system.memory.utilization", "system.disk.utilization"
RX = "system.network.rx_bytes"


@pytest.fixture
def client(app_engine):
    """The read API requires a verified Clerk session of an allowlisted user (see test_clerk_auth.py for the rejections)."""
    http = make_client(create_app(app_engine, TEST_CREDENTIALS, clock=lambda: FIXED_NOW, verifier=verifier()))
    http.headers.update(bearer())
    return http


@pytest.fixture
def seed(app_engine):
    factory = make_session_factory(app_engine)  # note: the app role has INSERT + SELECT only, which is all we need

    def _seed(host_id: str, sent_ago: float, metrics: list[dict], *, hostname: str | None = None,
              agent_id: str | None = None, service: str | None = None) -> None:
        envelope = {
            "schema_version": "1.0", "envelope_id": str(uuid.uuid4()), "agent_id": agent_id or f"agent-{host_id}",
            "host_id": host_id, "hostname": hostname or host_id, "timestamp": iso(sent_ago), "metrics": metrics,
        }
        if service:
            envelope["service"] = service
        store_envelope(factory, envelope, content_hash(envelope))

    return _seed


def by_id(body: dict) -> dict:
    return {h["host_id"]: h for h in body["items"]}


# ---------------------------------------------------------------------------------------------------- empty / errors

async def test_empty_database_returns_empty_pages_and_zero_facets(client):
    r = await client.get("/api/v1/hosts")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == [] and body["total"] == 0
    assert body["facets"]["source"] == {"live": 0, "synthetic": 0, "replay": 0}
    assert body["facets"]["health"] == {"healthy": 0, "warning": 0, "critical": 0, "offline": 0, "unknown": 0}


async def test_unknown_host_is_404_in_the_error_envelope(client):
    for path in ("/api/v1/hosts/nope", "/api/v1/hosts/nope/metrics/latest",
                 "/api/v1/hosts/nope/metrics/series?metric=system.cpu.utilization"):
        r = await client.get(path)
        assert r.status_code == 404, path
        assert r.json()["error"]["code"] == "host_not_found"


async def test_invalid_host_id_and_list_parameters_are_422(client):
    assert (await client.get("/api/v1/hosts/bad%20id")).status_code == 422
    for query in ("source=cloud", "health=great", "page=0", "page_size=500", "sort=cpu"):
        r = await client.get(f"/api/v1/hosts?{query}")
        assert r.status_code == 422, query
        assert r.json()["error"]["code"] == "invalid_request"


# ---------------------------------------------------------------------------------------------------- discovery

async def test_source_is_classified_and_only_live_hosts_are_connected_infrastructure(client, seed):
    for host in ("web-1", "sim-host-01", "sim3-host-02", "replay-smd-1"):
        seed(host, 30, [gauge(CPU, 10, 30)])
    items = by_id((await client.get("/api/v1/hosts")).json())
    assert {h: i["source"] for h, i in items.items()} == {
        "web-1": "live", "sim-host-01": "synthetic", "sim3-host-02": "synthetic", "replay-smd-1": "replay"}
    assert {h: i["connected_infrastructure"] for h, i in items.items()} == {
        "web-1": True, "sim-host-01": False, "sim3-host-02": False, "replay-smd-1": False}


async def test_source_filter_accepts_repeated_and_comma_forms_and_facets_ignore_their_own_filter(client, seed):
    for host in ("web-1", "web-2", "sim-host-01", "replay-smd-1"):
        seed(host, 30, [gauge(CPU, 10, 30)])
    body = (await client.get("/api/v1/hosts?source=synthetic")).json()
    assert [i["host_id"] for i in body["items"]] == ["sim-host-01"]
    assert body["facets"]["source"] == {"live": 2, "synthetic": 1, "replay": 1}  # still shows the other options
    both = (await client.get("/api/v1/hosts?source=live,replay")).json()
    again = (await client.get("/api/v1/hosts?source=live&source=replay")).json()
    assert both["total"] == again["total"] == 3


async def test_search_matches_hostname_or_host_id_case_insensitively_and_treats_wildcards_literally(client, seed):
    seed("laptop-ur6", 30, [gauge(CPU, 10, 30)], hostname="Dev-Win-01")
    seed("web-1", 30, [gauge(CPU, 10, 30)], hostname="frontend")
    assert [i["host_id"] for i in (await client.get("/api/v1/hosts?search=dev-win")).json()["items"]] == ["laptop-ur6"]
    assert [i["host_id"] for i in (await client.get("/api/v1/hosts?search=WEB")).json()["items"]] == ["web-1"]
    assert (await client.get("/api/v1/hosts?search=%25")).json()["total"] == 0  # '%' is not a wildcard


async def test_sort_and_pagination(client, seed):
    seed("b-host", 300, [gauge(CPU, 1, 300)], hostname="Bravo")
    seed("a-host", 60, [gauge(CPU, 1, 60)], hostname="alpha")
    seed("c-host", 10, [gauge(CPU, 1, 10)], hostname="Charlie")
    names = lambda b: [i["hostname"] for i in b["items"]]  # noqa: E731
    assert names((await client.get("/api/v1/hosts")).json()) == ["Charlie", "alpha", "Bravo"]  # last_seen desc
    assert names((await client.get("/api/v1/hosts?sort=hostname")).json()) == ["alpha", "Bravo", "Charlie"]  # case-insensitive
    assert names((await client.get("/api/v1/hosts?sort=hostname&order=desc")).json()) == ["Charlie", "Bravo", "alpha"]
    page = (await client.get("/api/v1/hosts?sort=hostname&page=2&page_size=2")).json()
    assert names(page) == ["Charlie"] and page["total"] == 3 and page["page"] == 2


# ---------------------------------------------------------------------------------------------------- health

async def test_health_rules_and_offline_threshold(client, seed):
    seed("ok", 30, [gauge(CPU, 20, 30), gauge(MEM, 40, 30), gauge(DISK, 50, 30, {"mount": "/"})])
    seed("warn", 30, [gauge(CPU, 20, 30), gauge(MEM, 88, 30)])
    seed("crit", 30, [gauge(CPU, 97, 30), gauge(DISK, 91, 30, {"mount": "C:"})])
    seed("edge", 30, [gauge(DISK, 95, 30, {"mount": "/"})])               # exactly at the critical threshold
    seed("quiet", 299, [gauge(CPU, 20, 299)])                              # just inside the offline limit
    seed("gone", 301, [gauge(CPU, 99, 301)])                               # just outside, and its last value was critical
    seed("sim-gone", 3000, [gauge(CPU, 20, 3000)])
    seed("replay-old", 86400, [gauge(CPU, 99, 86400)])                     # a day-old replay: not offline by the clock
    items = by_id((await client.get("/api/v1/hosts")).json())
    status = {h: i["health"]["status"] for h, i in items.items()}
    assert status == {"ok": "healthy", "warn": "warning", "crit": "critical", "edge": "critical", "quiet": "healthy",
                      "gone": "offline", "sim-gone": "offline", "replay-old": "critical"}
    assert items["gone"]["reporting_status"] == "silent" and items["replay-old"]["reporting_status"] == "replay"
    reasons = {(r["metric"], r["level"]) for r in items["crit"]["health"]["reasons"]}
    assert reasons == {(CPU, "critical"), (DISK, "warning")}
    assert (await client.get("/api/v1/hosts?health=critical,offline")).json()["total"] == 5


async def test_missing_metrics_are_unknown_not_healthy_and_never_zero(client, seed):
    seed("only-app", 30, [gauge("app.queue.depth", 5, 30, unit="count")])
    item = by_id((await client.get("/api/v1/hosts")).json())["only-app"]
    assert item["health"]["status"] == "unknown"
    assert item["latest"] == {"cpu_percent": None, "memory_percent": None, "disk_percent_max": None,
                              "disk_mount_max": None, "network_rx_bytes_per_second": None,
                              "network_tx_bytes_per_second": None}


# ---------------------------------------------------------------------------------------------------- host detail / latest

async def test_host_detail_identity_first_and_last_seen_and_latest_hostname(client, seed):
    seed("h1", 3600, [gauge(CPU, 10, 3600)], hostname="old-name", agent_id="agent-old", service="checkout-api")
    seed("h1", 20, [gauge(CPU, 33, 20)], hostname="new-name", agent_id="agent-new", service="checkout-api")
    h = (await client.get("/api/v1/hosts/h1")).json()
    assert (h["hostname"], h["agent_id"], h["service"], h["source"]) == ("new-name", "agent-new", "checkout-api", "live")
    assert h["first_seen"] == "2026-11-11T11:00:00Z" and h["last_seen"] == "2026-11-11T11:59:40Z"
    assert h["last_seen_age_seconds"] == 20
    assert h["reporting_status"] == "reporting" and h["latest"]["cpu_percent"] == 33
    assert "operating_system" not in h  # not collected by telemetry v1: never invented


async def test_latest_metric_selection_per_series_and_lookback(client, seed):
    seed("h1", 600, [gauge(CPU, 11, 600), gauge(MEM, 22, 600), gauge(DISK, 60, 600, {"mount": "C:"}),
                     gauge(DISK, 70, 600, {"mount": "D:"})])
    seed("h1", 30, [gauge(CPU, 44, 30), gauge(DISK, 61, 30, {"mount": "C:"})])  # newer cpu and C:, no mem / D:
    body = (await client.get("/api/v1/hosts/h1/metrics/latest")).json()
    got = {(m["name"], tuple(sorted(m["labels"].items()))): m for m in body["metrics"]}
    assert got[(CPU, ())]["value"] == 44
    assert got[(DISK, (("mount", "C:"),))]["value"] == 61
    assert got[(DISK, (("mount", "D:"),))]["value"] == 70   # only in the older envelope, still the latest D:
    assert got[(MEM, ())]["value"] == 22
    assert got[(MEM, ())]["stale"] is True                  # 570 s older than the host's last envelope
    assert by_id((await client.get("/api/v1/hosts")).json())["h1"]["latest"]["memory_percent"] is None  # stale: not a headline
    # Beyond the one-hour lookback of the host's last envelope a metric is simply absent (not zero).
    seed("h2", 7200, [gauge(MEM, 50, 7200)])
    seed("h2", 30, [gauge(CPU, 5, 30)])
    names = {m["name"] for m in (await client.get("/api/v1/hosts/h2/metrics/latest")).json()["metrics"]}
    assert names == {CPU}


async def test_network_counters_are_totals_with_a_rate_only_from_valid_consecutive_samples(client, seed):
    seed("h1", 120, [counter(RX, 1_000, 120)])
    seed("h1", 60, [counter(RX, 61_000, 60)])
    m = {x["name"]: x for x in (await client.get("/api/v1/hosts/h1/metrics/latest")).json()["metrics"]}[RX]
    assert m["kind"] == "counter" and m["value"] == 61_000 and m["rate_per_second"] == 1_000  # 60 000 B / 60 s
    seed("h2", 120, [counter(RX, 900_000, 120)])
    seed("h2", 60, [counter(RX, 500, 60)])                 # counter reset (reboot): no rate, not a negative one
    seed("h3", 3000, [counter(RX, 10, 3000)])
    seed("h3", 60, [counter(RX, 99, 60)])                  # samples too far apart to average across
    for host in ("h2", "h3"):
        mm = {x["name"]: x for x in (await client.get(f"/api/v1/hosts/{host}/metrics/latest")).json()["metrics"]}[RX]
        assert mm["rate_per_second"] is None, host
    assert by_id((await client.get("/api/v1/hosts")).json())["h1"]["latest"]["network_rx_bytes_per_second"] == 1_000


# ---------------------------------------------------------------------------------------------------- series

def series_url(host: str, metric: str, start: float, end: float, extra: str = "") -> str:
    return (f"/api/v1/hosts/{host}/metrics/series?metric={metric}&start={iso(start)}&end={iso(end)}{extra}")


async def test_gauge_series_is_chronological_bounded_and_marks_gaps_as_null(client, seed):
    # window = [NOW-600s, NOW), 60 s buckets: 10 buckets; samples at -540 (bucket 1), -530 (bucket 1), -300 (bucket 5)
    seed("h1", 20, [gauge(CPU, 10, 540), gauge(CPU, 30, 530), gauge(CPU, 50, 300),
                    gauge(CPU, 99, 700),    # before start: excluded
                    gauge(CPU, 98, 0.001)])  # inside, last bucket
    r = await client.get(series_url("h1", CPU, 600, 0, "&step=60"))
    assert r.status_code == 200
    body = r.json()
    assert body["step_seconds"] == 60 and body["kind"] == "gauge" and len(body["series"]) == 1
    pts = body["series"][0]["points"]
    assert len(pts) == 10
    times = [p["t"] for p in pts]
    assert times == sorted(times) and times[0].startswith(iso(600)[:19])
    values = [p["value"] for p in pts]
    assert values == [None, 20, None, None, None, 50, None, None, None, 98]    # avg of 10 and 30 is 20; gaps are null
    assert body["series"][0]["unit"] == "percent" and body["series"][0]["value_type"] == "gauge"
    mn = (await client.get(series_url("h1", CPU, 600, 0, "&step=60&agg=min"))).json()["series"][0]["points"]
    last = (await client.get(series_url("h1", CPU, 600, 0, "&step=60&agg=last"))).json()["series"][0]["points"]
    assert mn[1]["value"] == 10 and last[1]["value"] == 30 and mn[1]["samples"] == 2


async def test_series_start_is_inclusive_end_is_exclusive_and_uses_observation_time(client, seed):
    # one envelope sent after the window, carrying an observation from inside it (a buffered flush)
    seed("h1", 5, [gauge(CPU, 42, 120), gauge(CPU, 77, 60)])
    pts = (await client.get(series_url("h1", CPU, 180, 60, "&step=60"))).json()["series"][0]["points"]
    assert [p["value"] for p in pts] == [None, 42.0]          # 120 s ago is bucket 1; exactly `end` (60 s ago) is out


async def test_series_are_split_per_label_set(client, seed):
    seed("h1", 20, [gauge(DISK, 60, 100, {"mount": "C:"}), gauge(DISK, 80, 100, {"mount": "D:"})])
    series = (await client.get(series_url("h1", DISK, 300, 0, "&step=60"))).json()["series"]
    assert {tuple(s["labels"].items()): max(p["value"] or 0 for p in s["points"]) for s in series} == {
        (("mount", "C:"),): 60, (("mount", "D:"),): 80}


async def test_counter_series_is_a_rate_with_resets_and_missing_samples_left_as_gaps(client, seed):
    # 60 s samples. rx: 0, 6000, 12000, [reset] 300, 6300, then a 10 minute hole, then 7200 and 13200
    points = [(660, 0), (600, 6_000), (540, 12_000), (480, 300), (420, 6_300), (0, 13_200)]
    seed("h1", 5, [counter(RX, v, ago) for ago, v in points])
    body = (await client.get(series_url("h1", RX, 660, 0, "&step=60"))).json()
    assert body["kind"] == "counter" and body["series"][0]["value_type"] == "counter_rate"
    assert body["series"][0]["unit"] == "bytes_per_second"
    pts = body["series"][0]["points"]
    # bucket i covers [start+60i, start+60(i+1)); each rate is stamped at its later sample
    rates = {i: p["value"] for i, p in enumerate(pts) if p["value"] is not None}
    assert rates == {1: 100.0, 2: 100.0, 4: 100.0}   # 0->6000, 6000->12000, 300->6300. Reset interval and the hole: none
    assert all(v >= 0 for v in rates.values())


async def test_counter_rate_for_the_first_bucket_uses_the_sample_before_start(client, seed):
    seed("h1", 5, [counter(RX, 0, 90), counter(RX, 9_000, 30)])
    pts = (await client.get(series_url("h1", RX, 60, 0, "&step=60"))).json()["series"][0]["points"]
    assert [p["value"] for p in pts] == [150.0]   # 9 000 B over the 60 s between the two samples


async def test_series_unknown_metric_is_an_empty_series_and_bad_ranges_are_422(client, seed):
    seed("h1", 20, [gauge(CPU, 10, 20)])
    body = (await client.get(series_url("h1", "system.nope.metric", 600, 0))).json()
    assert body["kind"] is None and body["series"] == []
    base = "/api/v1/hosts/h1/metrics/series?metric=system.cpu.utilization"
    bad = [
        f"{base}&start=2026-11-11T12:00:00Z&end=2026-11-11T11:00:00Z",           # start after end
        f"{base}&start=2026-11-11T11:00:00Z&end=2026-11-11T11:00:00Z",           # empty
        f"{base}&start=2026-11-11T11:00:00&end=2026-11-11T12:00:00Z",            # naive timestamp
        f"{base}&start=2026-09-01T00:00:00Z&end=2026-11-11T00:00:00Z",           # > 31 days
        f"{base}&start=2026-11-11T00:00:00Z&end=2026-11-11T12:00:00Z&step=5",    # step too small
        f"{base}&start=2026-11-01T00:00:00Z&end=2026-11-11T00:00:00Z&step=60",   # too many points
        "/api/v1/hosts/h1/metrics/series",                                       # metric missing
        "/api/v1/hosts/h1/metrics/series?metric=Bad Name",                       # invalid metric name
    ]
    for url in bad:
        r = await client.get(url)
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request", url


async def test_default_range_is_the_last_six_hours_with_an_automatic_step(client, seed):
    seed("h1", 20, [gauge(CPU, 10, 20)])
    body = (await client.get("/api/v1/hosts/h1/metrics/series?metric=system.cpu.utilization")).json()
    assert body["step_seconds"] == 60 and len(body["series"][0]["points"]) == 360
    assert body["end"].startswith("2026-11-11T12:00:00") and body["start"].startswith("2026-11-11T06:00:00")


async def test_read_api_works_with_the_least_privilege_role_and_never_writes(client, seed, engine):
    seed("h1", 20, [gauge(CPU, 10, 20)])
    from sqlalchemy import text
    with engine.connect() as c:
        before = c.execute(text("SELECT count(*) FROM telemetry_envelopes")).scalar_one()
    for path in ("/api/v1/hosts", "/api/v1/hosts/h1", "/api/v1/hosts/h1/metrics/latest",
                 "/api/v1/hosts/h1/metrics/series?metric=system.cpu.utilization"):
        assert (await client.get(path)).status_code == 200, path
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM telemetry_envelopes")).scalar_one() == before
