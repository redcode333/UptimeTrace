"""The simulator's output must be contract-valid, reproducible, and talk real HTTP."""
import json
import random
import threading
from datetime import UTC, datetime

import httpx
import pytest
import uvicorn
from sqlalchemy import text

from app.main import create_app
from app.ingest.schema import EnvelopeSchemaValidator
from app.ingest.semantic import validate_semantics
from app.config import SCHEMA_PATH
from simulator.runner import run
from simulator.scenarios import SCENARIOS, State, build_envelope
from tests.conftest import TEST_CREDENTIALS, TEST_TOKEN

NOW = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
COMMON = dict(agent_id="a-1", host_id="sim-h-1", hostname="h-1.local", service="svc")


def gen(scenario, seed, n=30):
    rng, id_rng = random.Random(seed), random.Random(f"ids-{seed}")
    state = State(rx=10**9, tx=10**9)
    return [build_envelope(scenario, t, n, rng, state, id_rng, now=NOW, interval=1.0, **COMMON) for t in range(n)]


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_every_scenario_is_valid_against_schema_and_semantics(scenario):
    validator = EnvelopeSchemaValidator(SCHEMA_PATH)
    for envelope in gen(scenario, seed=3):
        assert validator.validate(envelope) == []
        assert validate_semantics(envelope, NOW) == []


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_same_seed_reproduces_and_different_seed_differs(scenario):
    assert gen(scenario, 5) == gen(scenario, 5)
    assert gen(scenario, 5) != gen(scenario, 6)


def test_scenarios_have_their_signature_shape():
    def series(scenario, metric, mount=None):
        out = []
        for e in gen(scenario, 1, 40):
            for m in e["metrics"]:
                if m["name"] == metric and (mount is None or m["labels"]["mount"] == mount):
                    out.append(m["value"])
        return out

    assert max(series("normal_baseline", "system.cpu.utilization")) < 50
    assert min(series("cpu_saturation", "system.cpu.utilization")[15:]) > 90
    mem = series("memory_pressure", "system.memory.utilization")
    assert mem[0] < 55 and mem[-1] > 90
    disk = series("disk_pressure", "system.disk.utilization", "/")
    assert disk[0] < 65 and disk[-1] > 94
    rx = series("network_spike", "system.network.rx_bytes")
    deltas = [b - a for a, b in zip(rx, rx[1:])]
    assert max(deltas) > 10 * sorted(deltas)[len(deltas) // 4]
    cpu = series("gradual_degradation", "system.cpu.utilization")
    assert sum(cpu[-5:]) / 5 > sum(cpu[:5]) / 5 + 40


def test_network_counters_never_decrease():
    for scenario in SCENARIOS:
        prev = {}
        for e in gen(scenario, 2):
            for m in e["metrics"]:
                if m["kind"] == "counter":
                    assert m["value"] >= prev.get(m["name"], 0)
                    prev[m["name"]] = m["value"]


@pytest.fixture
def live_server(app_engine):
    """A real uvicorn server on a real socket, backed by the test database."""
    config = uvicorn.Config(create_app(app_engine, TEST_CREDENTIALS), host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        thread.join(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def test_simulator_over_real_http_is_accepted_and_persisted(live_server, engine):
    summary = run(url=live_server, scenario="gradual_degradation", count=6, interval=0, seed=9,
                  sleep=lambda _s: None, token=TEST_TOKEN, **COMMON, )
    assert (summary.accepted, summary.rejected, summary.failed) == (6, 0, 0)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM telemetry_envelopes WHERE host_id = 'sim-h-1'")).scalar_one() == 6


def test_simulator_reports_a_dead_server_as_failed_not_accepted():
    summary = run(url="http://127.0.0.1:1", scenario="normal_baseline", count=3, interval=0, seed=1,
                  sleep=lambda _s: None, **COMMON)
    assert (summary.accepted, summary.rejected, summary.failed) == (0, 0, 3)


def test_simulator_counts_a_server_refusal_as_rejected():
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": {"code": "validation_error", "message": "x",
                                                   "details": [{"path": "metrics[0]", "message": "bad"}]}})

    client = httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://x")
    summary = run(url="http://x", scenario="normal_baseline", count=2, interval=0, seed=1,
                  client=client, sleep=lambda _s: None, **COMMON)
    assert (summary.accepted, summary.rejected, summary.failed) == (0, 2, 0)
    assert "metrics[0]: bad" in summary.problems[0]


def test_same_seed_in_two_runs_or_on_two_hosts_never_reuses_envelope_ids():
    """Regression: ids once came from the seeded value RNG, so a second run was a 409 conflict."""
    def ids(host, started):
        sent = []
        client = httpx.Client(transport=httpx.MockTransport(
            lambda r: sent.append(json.loads(r.content)["envelope_id"]) or httpx.Response(202)), base_url="http://x")
        run(url="http://x", scenario="normal_baseline", count=5, interval=0, seed=1, client=client,
            sleep=lambda _s: None, now=lambda: started, **{**COMMON, "host_id": host})
        return sent

    from datetime import timedelta
    a = ids("sim-h-1", NOW)
    assert len(set(a)) == 5
    assert not set(a) & set(ids("sim-h-2", NOW))                      # other host, same seed
    assert not set(a) & set(ids("sim-h-1", NOW + timedelta(seconds=1)))  # re-run, same seed and host
    assert a == ids("sim-h-1", NOW)                                    # identical run => identical ids


@pytest.mark.parametrize("host, ok", [("sim-web-01", True), ("sim2-cpu_saturation", True), ("web-01", False),
                                      ("simulator-1", False), ("prod-sim-1", False)])
def test_simulator_host_ids_must_stay_identifiable(host, ok, capsys):
    from simulator.__main__ import main
    argv = ["--host-id", host, "--count", "1", "--interval", "0", "--url", "http://127.0.0.1:1"]
    if ok:
        assert main(argv) == 1            # parsed fine; nothing listens on port 1, so the send fails
    else:
        with pytest.raises(SystemExit) as exit_info:
            main(argv)
        assert exit_info.value.code == 2
