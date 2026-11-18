"""The real Go agent against the real FastAPI app: contract validity, bearer authentication, host scope and graceful shutdown.

The agent binary is built from ../agent and run as a separate process talking real HTTP to a real uvicorn server. Only the
database write is replaced by a spy (no PostgreSQL needed); everything before it - authentication, scope check, JSON schema,
semantic checks - is the production code path.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import create_engine

from app import auth, main
from app.auth import CredentialStore
from app.config import SCHEMA_PATH
from app.ingest.schema import EnvelopeSchemaValidator
from app.ingest.semantic import validate_semantics
from app.ingest.service import Outcome

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"
WELL_KNOWN = {"system.cpu.utilization": ("gauge", "percent", None), "system.memory.utilization": ("gauge", "percent", None),
              "system.disk.utilization": ("gauge", "percent", "mount"), "system.network.rx_bytes": ("counter", "bytes", "interface"),
              "system.network.tx_bytes": ("counter", "bytes", "interface")}


def find_go():
    return shutil.which("go") or next((p for p in (r"C:\Program Files\Go\bin\go.exe", "/usr/local/go/bin/go") if os.path.exists(p)), None)


@pytest.fixture(scope="module")
def agent_binary(tmp_path_factory):
    go = find_go()
    if go is None:
        pytest.skip("Go is not installed")
    out = tmp_path_factory.mktemp("agent") / ("agent.exe" if os.name == "nt" else "agent")
    r = subprocess.run([go, "build", "-o", str(out), "./cmd/agent"], cwd=AGENT_DIR, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    return out


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Real uvicorn + the production create_app; credentials issued through the real CLI path; storage replaced by a spy."""
    cred_file = tmp_path / "credentials.json"
    token = auth.issue(cred_file, "agent-under-test", "agent-poc", ["poc-host"], "live")
    stored = []
    monkeypatch.setattr(main, "store_envelope", lambda sf, env, digest: stored.append(env) or Outcome.CREATED)
    app = main.create_app(create_engine("sqlite://"), CredentialStore.load(cred_file))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        thread.join(0.01)
    port = srv.servers[0].sockets[0].getsockname()[1]
    yield {"url": f"http://127.0.0.1:{port}", "token": token, "stored": stored}
    srv.should_exit = True
    thread.join(5)


def run_agent(binary, *, url, token, host_id="poc-host", agent_id="agent-poc", seconds=3.5, extra=()):
    env = {**os.environ, "UPTIMETRACE_URL": url, "UPTIMETRACE_TOKEN": token}
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
    p = subprocess.Popen([str(binary), "-host-id", host_id, "-agent-id", agent_id, "-interval", "1s", "-flush-interval", "1s", *extra],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)
    time.sleep(seconds)
    p.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)     # a real graceful-shutdown request
    try:
        _, err = p.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        raise
    return p.returncode, err


def test_dry_run_envelope_passes_the_contract_schema_and_semantic_checks(agent_binary):
    r = subprocess.run([str(agent_binary), "-dry-run", "-host-id", "poc-host", "-agent-id", "agent-poc"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    envelopes = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    assert envelopes
    validator = EnvelopeSchemaValidator(SCHEMA_PATH)
    for env in envelopes:
        assert validator.validate(env) == []
        assert validate_semantics(env, datetime.now(UTC)) == []
        assert 1 <= len(env["metrics"]) <= 500
        names = {m["name"] for m in env["metrics"]}
        assert {"system.cpu.utilization", "system.memory.utilization", "system.disk.utilization"} <= names
        for m in env["metrics"]:
            kind, unit, label = WELL_KNOWN[m["name"]]                   # nothing outside the contract's well-known set
            assert (m["kind"], m["unit"]) == (kind, unit)
            assert set(m.get("labels", {})) == ({label} if label else set())
            if m["kind"] == "counter":
                assert isinstance(m["value"], int) and m["value"] >= 0
            else:
                assert 0 <= m["value"] <= 100


def test_agent_reports_to_the_real_api_with_its_credential_and_stops_gracefully(agent_binary, server):
    code, err = run_agent(agent_binary, url=server["url"], token=server["token"])
    assert code == 0, err
    stored = server["stored"]
    assert len(stored) >= 2, err
    assert {e["host_id"] for e in stored} == {"poc-host"} and {e["agent_id"] for e in stored} == {"agent-poc"}
    # Delivery is at-least-once: a POST interrupted by shutdown after the server stored it is re-sent by Drain with the SAME
    # bytes (the real API answers that idempotently). The spy storage does not dedupe, so a repeated envelope_id is fine
    # provided the content is identical; a different body under one id would be a real defect (409 in production).
    by_id = {}
    for e in stored:
        assert by_id.setdefault(e["envelope_id"], e) == e
    assert len(by_id) >= 2
    validator = EnvelopeSchemaValidator(SCHEMA_PATH)
    assert all(validator.validate(e) == [] for e in stored)
    assert server["token"] not in err and "agent stopped" in err and "undelivered=0" in err


def test_wrong_token_is_rejected_once_per_envelope_and_never_retried(agent_binary, server):
    bad = "utk_" + "Z" * 43
    code, err = run_agent(agent_binary, url=server["url"], token=bad, seconds=2.5)
    assert code == 0 and server["stored"] == []
    assert "HTTP 401 (unauthorized)" in err and "check the bearer token" in err and "retry" not in err.lower().replace("retries=0", "")
    assert bad not in err


def test_valid_token_for_the_wrong_host_is_forbidden_and_stores_nothing(agent_binary, server):
    code, err = run_agent(agent_binary, url=server["url"], token=server["token"], host_id="someone-elses-host", seconds=2.5)
    assert code == 0 and server["stored"] == []
    assert "HTTP 403 (forbidden)" in err and server["token"] not in err
