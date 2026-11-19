"""End-to-end product workflow: workspace -> host registration -> credential -> real Go agent -> ingestion -> persistence ->
monitoring -> controlled threshold breach -> persistent alert -> acknowledge -> recovery -> silence -> offline -> restart -> revocation.

Runs against an ISOLATED local stack only:
  * PostgreSQL in a disposable container, database `uptimetrace_e2e` (created by the operator; this script refuses a non-empty one);
  * the real API (scripts/e2e/app.py, the production app factory) on 127.0.0.1;
  * the real Go agent, built from agent/ and started as a child process.

Environment (never written to disk by this script):
  E2E_OWNER_URL   postgresql://postgres:<pw>@127.0.0.1:<port>/uptimetrace_e2e      (migrations, role password, read-back checks)
  E2E_PORT        optional, default 8765

Writes docs/evidence/e2e_workflow.json (no tokens, no passwords) and a transcript under the scratchpad.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import sqlalchemy as sa

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
AGENT_DIR = REPO / "agent"
EVIDENCE = REPO / "docs" / "evidence" / "e2e_workflow.json"
SCRATCH = Path(os.environ.get("E2E_SCRATCH", Path(os.environ.get("TEMP", ".")) / "uptimetrace-e2e"))
PORT = int(os.environ.get("E2E_PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"
AGENT_HOST = "e2e-agent-01"
CTRL_HOST = "e2e-controlled-01"
CPU = "system.cpu.utilization"

sys.path.insert(0, str(BACKEND))
from tests.clerk_support import mint  # noqa: E402  (local test key set; no Clerk tenant is available here)

steps: list[dict] = []
procs: list[subprocess.Popen] = []


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def record(name: str, ok: bool, **detail) -> None:
    steps.append({"step": name, "ok": bool(ok), "at": iso(now()), **detail})
    print(("PASS " if ok else "FAIL ") + name + (("  " + json.dumps(detail, default=str)[:300]) if detail else ""), flush=True)
    if not ok:
        raise SystemExit(f"step failed: {name}")


def wait_until(predicate, timeout: float, what: str, every: float = 1.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(every)
    raise SystemExit(f"timed out after {timeout:.0f}s waiting for: {what} (last={json.dumps(last, default=str)[:200]})")


def owner_engine():
    return sa.create_engine(os.environ["E2E_OWNER_URL"].replace("postgresql://", "postgresql+psycopg://", 1))


def envelope(host_id: str, cpu: float | None, when: dt.datetime, agent_id: str) -> dict:
    metrics = []
    if cpu is not None:
        metrics.append({"name": CPU, "kind": "gauge", "value": cpu, "unit": "percent", "timestamp": iso(when - dt.timedelta(milliseconds=500))})
    return {"schema_version": "1.0", "envelope_id": str(uuid.uuid4()), "agent_id": agent_id, "host_id": host_id,
            "hostname": host_id, "timestamp": iso(when), "metrics": metrics}


def main() -> int:
    # One test signing key for this run, shared with the API child process (which verifies the browser token).
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    os.environ["E2E_CLERK_PRIVATE_KEY_PEM"] = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    owner_url = os.environ["E2E_OWNER_URL"]
    host_port = urlsplit(owner_url).port
    if not host_port or urlsplit(owner_url).path.strip("/") != "uptimetrace_e2e":
        raise SystemExit("E2E_OWNER_URL must point at the database uptimetrace_e2e (never another database)")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    eng = owner_engine()
    with eng.connect() as c:
        tables = c.execute(sa.text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")).scalar_one()
    if tables:
        raise SystemExit("uptimetrace_e2e is not empty; refusing to run (it must be a fresh disposable database)")

    app_pw = secrets.token_urlsafe(24)
    app_url = urlunsplit(("postgresql", f"uptimetrace_app:{app_pw}@{urlsplit(owner_url).hostname}:{host_port}", "/uptimetrace_e2e", "", ""))
    env = {**os.environ, "DATABASE_URL": owner_url, "MIGRATION_DATABASE_URL": owner_url, "PYTHONUNBUFFERED": "1"}

    # ---------------------------------------------------------------- 1. database: real migrations, real runtime role
    mig = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, capture_output=True, text=True)
    record("migrations applied to a fresh database (alembic upgrade head)", mig.returncode == 0, returncode=mig.returncode)
    with eng.begin() as c:
        c.execute(sa.text(f"ALTER ROLE uptimetrace_app LOGIN PASSWORD '{app_pw}'"))
    record("runtime role uptimetrace_app enabled (least privilege, as in production)", True)

    # ---------------------------------------------------------------- 2. start the real API
    api_log = open(SCRATCH / "api.log", "w", encoding="utf-8")
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "scripts.e2e.app:app", "--host", "127.0.0.1", "--port", str(PORT)],
                           cwd=REPO, env={**env, "E2E_APP_DATABASE_URL": app_url}, stdout=api_log, stderr=subprocess.STDOUT)
    procs.append(api)
    wait_until(lambda: _get("/health") and _get("/health").get("status") == "ok", 60, "API health")
    record("real API started on 127.0.0.1 with the production app factory", True, base=BASE)

    token = mint(exp_in=3600)                         # browser session (local test key set)
    H = {"Authorization": f"Bearer {token}"}
    http = httpx.Client(base_url=BASE, timeout=10)

    # ---------------------------------------------------------------- 3. workspace monitoring settings (short windows for the demo)
    r = http.put("/api/v1/settings/monitoring", headers=H, json={"expected_version": 0, "config": {
        "freshness": {"stale_after_seconds": 10, "offline_after_seconds": 20},
        "rules": {"system.cpu.utilization": {"warning": 85, "critical": 95}, "system.memory.utilization": {"warning": 85, "critical": 95},
                  "system.disk.utilization": {"warning": 90, "critical": 95}}}})
    record("workspace monitoring settings saved (stale 10 s, offline 20 s for this run)", r.status_code == 200, status=r.status_code)

    # ---------------------------------------------------------------- 4. host registration and credentials
    creds = {}
    for host in (AGENT_HOST, CTRL_HOST):
        r = http.post("/api/v1/registry/hosts", headers=H, json={"host_id": host, "kind": "live"})
        record(f"host registered: {host}", r.status_code == 201, status=r.status_code)
        r = http.post(f"/api/v1/registry/hosts/{host}/credentials", headers=H, json={})
        record(f"credential issued for {host} (shown once; kept only in this process)", r.status_code == 201, status=r.status_code,
               token_returned=bool(r.json().get("token")), cache_control=r.headers.get("cache-control"))
        creds[host] = {"token": r.json()["token"], "id": r.json()["id"], "agent_id": r.json()["agent_id"]}
    r = http.get(f"/api/v1/registry/hosts/{AGENT_HOST}/credentials", headers=H)
    record("credential listing never contains the token", "token" not in json.dumps(r.json()), count=len(r.json()))

    # ---------------------------------------------------------------- 5. build and start the REAL agent
    exe = SCRATCH / "uptimetrace-agent.exe"
    b = subprocess.run(["go", "build", "-o", str(exe), "./cmd/agent"], cwd=AGENT_DIR, capture_output=True, text=True)
    record("Go agent built from source (go build ./cmd/agent)", b.returncode == 0 and exe.exists(), returncode=b.returncode)
    agent_log_n = [0]

    def start_agent(label: str, token_value: str) -> subprocess.Popen:
        agent_log_n[0] += 1
        path = SCRATCH / f"agent-{agent_log_n[0]}-{label}.log"
        out = open(path, "w", encoding="utf-8")
        p = subprocess.Popen([str(exe), "-interval", "5s", "-flush-interval", "5s"], cwd=SCRATCH,
                             env={**os.environ, "UPTIMETRACE_URL": BASE, "UPTIMETRACE_TOKEN": token_value,
                                  "UPTIMETRACE_HOST_ID": AGENT_HOST, "UPTIMETRACE_AGENT_ID": creds[AGENT_HOST]["agent_id"],
                                  "UPTIMETRACE_HOSTNAME": AGENT_HOST},
                             stdout=out, stderr=subprocess.STDOUT)
        procs.append(p)
        return p, path

    agent, agent_log = start_agent("first", creds[AGENT_HOST]["token"])
    record("agent started with its registry credential (UPTIMETRACE_TOKEN, never a flag)", agent.poll() is None, pid=agent.pid)

    def host_view(host: str):
        r = http.get(f"/api/v1/hosts/{host}", headers=H)
        return r.json() if r.status_code == 200 else None

    first = wait_until(lambda: (lambda v: v if v and v["health"]["status"] != "unknown" and v["reporting_status"] == "reporting" else None)(host_view(AGENT_HOST)),
                       90, "agent telemetry through the API")
    cpu_real = first["latest"]["cpu_percent"]
    record("real telemetry reached the API and is shown for the host", cpu_real is not None,
           cpu_percent=cpu_real, memory_percent=first["latest"]["memory_percent"], reporting=first["reporting_status"],
           health=first["health"]["status"], last_seen_age_s=round(first["last_seen_age_seconds"], 1))

    with eng.connect() as c:
        n = c.execute(sa.text("SELECT count(*) FROM telemetry_envelopes WHERE host_id = :h"), {"h": AGENT_HOST}).scalar_one()
        payload = c.execute(sa.text("SELECT payload FROM telemetry_envelopes WHERE host_id = :h ORDER BY id DESC LIMIT 1"), {"h": AGENT_HOST}).scalar_one()
    record("telemetry persisted in PostgreSQL (rows read back with the owner role)", n > 0 and CPU in json.dumps(payload), rows=n)

    # ---------------------------------------------------------------- 6. controlled breach through the SAME signed ingestion path
    def post(host: str, cpu: float | None, agent_id: str | None = None):
        body = envelope(host, cpu, now(), agent_id or creds[host]["agent_id"])
        return http.post("/api/v1/telemetry", headers={"Authorization": f"Bearer {creds[host]['token']}"}, json=body)

    r = post(CTRL_HOST, 22.0)
    record("controlled host reports normal CPU (22%)", r.status_code == 202, status=r.status_code)
    record("controlled host shows as healthy while its data is fresh", host_view(CTRL_HOST)["health"]["status"] == "healthy")

    r = post(CTRL_HOST, 97.0)
    record("controlled breach sent: CPU 97% through the signed ingestion path", r.status_code == 202, status=r.status_code)
    alert = wait_until(lambda: _active(http, H, CTRL_HOST, CPU), 15, "persistent alert for the breach")
    record("persistent alert opened from the breach (critical, once)", alert["severity"] == "critical" and alert["state"] == "open",
           alert_id=alert["id"], severity=alert["severity"], value=alert["value"], threshold=alert["threshold"])
    record("host health reports critical from the same data", host_view(CTRL_HOST)["health"]["status"] == "critical")
    post(CTRL_HOST, 98.0)
    time.sleep(3)
    dup = [a for a in http.get("/api/v1/alerts?status=active&host_id=" + CTRL_HOST, headers=H).json()["alerts"] if a["condition_key"] == CPU + "|{}"]
    record("a repeated breach does not create a duplicate alert", len(dup) == 1 and dup[0]["id"] == alert["id"], alerts=len(dup))

    r = http.post(f"/api/v1/alerts/{alert['id']}/acknowledge", headers=H)
    record("alert acknowledged by the operator", r.status_code == 200 and r.json()["state"] == "acknowledged", acknowledged_by=r.json().get("acknowledged_by"))

    post(CTRL_HOST, 20.0)
    recovered = wait_until(lambda: _resolved(http, H, alert["id"]), 15, "recovery to resolve the alert")
    record("normal telemetry restored: alert resolved by recovery (not by the operator)", recovered["resolution"] == "recovered" and recovered["resolved_by"] == "system",
           resolution=recovered["resolution"], resolved_by=recovered["resolved_by"])
    tl = http.get(f"/api/v1/alerts/{alert['id']}", headers=H).json()["events"]
    record("alert timeline records opened, acknowledged and recovered", [e["event"] for e in tl] == ["opened", "acknowledged", "recovered"],
           events=[e["event"] for e in tl])

    # ---------------------------------------------------------------- 7. silence: stop controlled telemetry
    silence_started = now()
    statuses = []
    def offline_seen():
        v = host_view(CTRL_HOST)
        statuses.append((round((now() - silence_started).total_seconds()), v["reporting_status"], v["health"]["status"]))
        return v["health"]["status"] == "offline"
    wait_until(offline_seen, 90, "controlled host to become offline")
    bad = [s for s in statuses if s[1] != "reporting" and s[2] == "healthy"]
    record("silent host never reported as healthy", not bad, observations=statuses[-6:])
    host_off = wait_until(lambda: _active(http, H, CTRL_HOST, "host|offline"), 15, "host_offline alert")
    record("silent host raised a persistent host_offline alert", host_off["rule"] == "host_offline" and host_off["severity"] == "critical",
           alert_id=host_off["id"])

    post_resume = post(CTRL_HOST, 20.0)
    gone = wait_until(lambda: not _active(http, H, CTRL_HOST, "host|offline"), 15, "host_offline recovery")
    record("controlled host resumed: host_offline alert recovered", post_resume.status_code == 202 and gone is True)

    # ---------------------------------------------------------------- 8. stop the REAL agent (machine/process stop), observe stale -> offline
    agent.kill()
    agent.wait(timeout=10)
    stop_at = now()
    seen = []
    def agent_offline():
        v = host_view(AGENT_HOST)
        seen.append((round((now() - stop_at).total_seconds()), v["reporting_status"], v["health"]["status"]))
        return v["health"]["status"] == "offline"
    wait_until(agent_offline, 90, "agent host to become offline after its process stopped")
    stale_seen = any(s[1] == "stale" for s in seen)
    record("stopped agent: reporting -> stale -> offline (not healthy at any point)",
           stale_seen and not any(s[2] == "healthy" for s in seen if s[1] != "reporting"), observations=seen[-8:])
    wait_until(lambda: _active(http, H, AGENT_HOST, "host|offline"), 15, "agent host_offline alert")
    record("stopped agent raised a host_offline alert", True)

    # ---------------------------------------------------------------- 9. restart the agent: connectivity recovers
    agent, agent_log = start_agent("restart", creds[AGENT_HOST]["token"])
    back = wait_until(lambda: (lambda v: v if v and v["reporting_status"] == "reporting" and v["health"]["status"] != "offline" else None)(host_view(AGENT_HOST)),
                      90, "agent reporting again")
    record("restarted agent reports again: host reporting", True, health=back["health"]["status"], cpu_percent=back["latest"]["cpu_percent"])
    wait_until(lambda: not _active(http, H, AGENT_HOST, "host|offline"), 30, "agent host_offline alert to recover")
    record("agent recovery resolved its host_offline alert", True)

    # ---------------------------------------------------------------- 10. credential revocation: immediate and safe
    cred_id = creds[AGENT_HOST]["id"]
    r = http.delete(f"/api/v1/registry/hosts/{AGENT_HOST}/credentials/{cred_id}", headers=H)
    record("credential revoked from the console API", r.status_code == 200 and r.json()["revoked_at"] is not None, status=r.status_code)
    probe = http.post("/api/v1/telemetry", headers={"Authorization": f"Bearer {creds[AGENT_HOST]['token']}"},
                      json=envelope(AGENT_HOST, 10.0, now(), creds[AGENT_HOST]["agent_id"]))
    record("revoked credential refused on the very next request (401)", probe.status_code == 401, status=probe.status_code)
    agent.kill()
    agent.wait(timeout=10)
    with eng.connect() as c:
        before = c.execute(sa.text("SELECT count(*) FROM telemetry_envelopes WHERE host_id = :h"), {"h": AGENT_HOST}).scalar_one()
    agent, agent_log = start_agent("revoked", creds[AGENT_HOST]["token"])
    time.sleep(20)
    with eng.connect() as c:
        after = c.execute(sa.text("SELECT count(*) FROM telemetry_envelopes WHERE host_id = :h"), {"h": AGENT_HOST}).scalar_one()
    log_text = agent_log.read_text(encoding="utf-8", errors="replace")
    record("agent with a revoked credential: nothing new persisted, agent keeps running, rejection is logged",
           after == before and agent.poll() is None and "401" in log_text, persisted_before=before, persisted_after=after,
           agent_running=agent.poll() is None, rejected_logged="401" in log_text)
    agent.kill()
    agent.wait(timeout=10)

    # ---------------------------------------------------------------- 11. read-back from the database
    with eng.connect() as c:
        per_host = dict(c.execute(sa.text("SELECT host_id, count(*) FROM telemetry_envelopes GROUP BY host_id")).all())
        alert_rows = c.execute(sa.text("SELECT host_id, rule, state, resolution, severity FROM alerts ORDER BY opened_at")).all()
        events = c.execute(sa.text("SELECT event, count(*) FROM alert_events GROUP BY event ORDER BY event")).all()
    record("database read-back: telemetry per host", True, envelopes_per_host=per_host)
    record("database read-back: alert rows and timeline events", True, alerts=[list(r) for r in alert_rows], events=dict(events))

    api.terminate()
    api.wait(timeout=15)
    summary = {"ok": all(s["ok"] for s in steps), "steps": len(steps), "finished_at": iso(now()),
               "environment": {"database": "disposable PostgreSQL 16 (container ut-e2e-pg, loopback), database uptimetrace_e2e",
                               "api": "production app factory via scripts/e2e/app.py, real migrations, runtime role uptimetrace_app",
                               "agent": "Go agent built from agent/ (go build ./cmd/agent) and run as a child process",
                               "clerk": "NOT the Clerk tenant: browser session tokens use the local test key set (tests/clerk_support.py)",
                               "clock": "real clock; alert sweep every 2 s; workspace stale 10 s, offline 20 s (production defaults 90 s / 300 s)"},
               "not_covered": ["browser click-through (no Clerk session is available in this environment)",
                               "Clerk sign-in and allowlist in a real tenant", "agent disk spool (the agent buffer is in memory)",
                               "stale/offline defaults (run used shortened windows)"],
               "steps_detail": steps}
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(summary, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"\nEND-TO-END RESULT: {'PASS' if summary['ok'] else 'FAIL'} ({len(steps)} steps)  evidence: {EVIDENCE}")
    return 0 if summary["ok"] else 1


def _get(path: str):
    try:
        r = httpx.get(BASE + path, timeout=3)
        return r.json()
    except Exception:
        return None


def _active(http, H, host: str, key: str):
    rows = http.get(f"/api/v1/alerts?status=active&host_id={host}", headers=H).json()["alerts"]
    if key == "host|offline":
        return next((a for a in rows if a["rule"] == "host_offline"), None)
    return next((a for a in rows if a["condition_key"] == key + "|{}"), None)


def _resolved(http, H, alert_id: str):
    a = http.get(f"/api/v1/alerts/{alert_id}", headers=H).json()
    return a if a["state"] == "resolved" else None


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except SystemExit as exc:
        print(f"\nEND-TO-END RESULT: FAIL: {exc}", flush=True)
        code = 1
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
    raise SystemExit(code)
