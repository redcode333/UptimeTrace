"""M1 acceptance: real Go agent -> real FastAPI (uvicorn, real HTTP) -> real PostgreSQL (disposable, loopback only).

What is real: the compiled agent binary, the production `create_app`, the registry/ingest/read code, PostgreSQL, the migrations.
What is NOT real: Clerk. The console API verifies locally minted RS256 JWTs (tests/clerk_support.py) because no Clerk session can
be created here. Everything else on the path is production code.

Run (from backend/, with the loopback test container up and TEST_DATABASE_URL set in backend/.env):
    python ../scripts/e2e_m1_onboarding.py            # drops/creates database ut_e2e in the TEST cluster only
    python ../scripts/e2e_m1_onboarding.py --keep     # keep the database afterwards for inspection
Evidence is printed and written to docs/evidence/m1_e2e.txt. Exit code 0 only if every check passed.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.db import make_engine, make_session_factory  # noqa: E402
from app.main import create_app  # noqa: E402
from app.registry.store import RegistryCredentialStore  # noqa: E402
from tests.clerk_support import bearer, verifier  # noqa: E402
from tests.conftest import alembic_config  # noqa: E402

DB_NAME = "ut_e2e"
HOST = "e2e-host-1"
AGENT = "agent-e2e-host-1"
LOG: list[str] = []
FAILS: list[str] = []


def say(msg: str):
    print(msg, flush=True)
    LOG.append(msg)


def check(ok: bool, what: str, detail: str = ""):
    say(f"[{'PASS' if ok else 'FAIL'}] {what}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(what)


def loopback_test_url() -> str:
    env = {**dotenv_values(BACKEND / ".env"), **os.environ}
    url = env.get("TEST_DATABASE_URL")
    if not url:
        sys.exit("TEST_DATABASE_URL is not set; refusing to run.")
    host = (urlsplit(url).hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"} or url == env.get("DATABASE_URL") or "supabase" in host:
        sys.exit("TEST_DATABASE_URL must be a loopback throwaway database; refusing to run.")
    return url


def build_agent(tmp: Path) -> Path:
    out = tmp / ("agent.exe" if os.name == "nt" else "agent")
    go = shutil.which("go")
    r = subprocess.run([go, "build", "-o", str(out), "./cmd/agent"], cwd=ROOT / "agent", capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        sys.exit("go build failed: " + r.stderr[-400:])
    return out


class Agent:
    def __init__(self, binary: Path, url: str, token: str, tmp: Path, tag: str):
        self.log = tmp / f"agent-{tag}.log"
        env = {**os.environ, "UPTIMETRACE_URL": url, "UPTIMETRACE_TOKEN": token}
        kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
        self.fh = open(self.log, "w", encoding="utf-8")
        self.p = subprocess.Popen([str(binary), "-host-id", HOST, "-agent-id", AGENT, "-interval", "1s", "-flush-interval", "1s"],
                                  env=env, stdout=self.fh, stderr=subprocess.STDOUT, text=True, **kw)

    def stop(self):
        if self.p.poll() is None:
            self.p.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            try:
                self.p.wait(30)
            except subprocess.TimeoutExpired:
                self.p.kill()
        self.fh.close()

    def text(self) -> str:
        self.fh.flush()
        return self.log.read_text(encoding="utf-8", errors="replace")


def wait_for(fn, timeout, every=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    test_url = loopback_test_url()
    base_url = make_url(test_url)
    admin_url = base_url.set(database="postgres").render_as_string(hide_password=False)
    admin = create_engine(admin_url, pool_pre_ping=True)
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{DB_NAME}"'))
    owner_url = base_url.set(database=DB_NAME).render_as_string(hide_password=False)
    owner = create_engine(owner_url)
    with owner.begin() as c:   # Supabase-like roles and default grants, as in the real target
        for role in ("anon", "authenticated", "service_role"):
            c.execute(text(f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN CREATE ROLE {role} NOLOGIN; END IF; END $$"))
        c.execute(text("GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role"))
        c.execute(text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role"))
    command.upgrade(alembic_config(owner_url), "head")
    pw = secrets.token_hex(16)
    with owner.begin() as c:
        c.execute(text(f"ALTER ROLE uptimetrace_app LOGIN PASSWORD '{pw}'"))
    app_url = base_url.set(database=DB_NAME, username="uptimetrace_app", password=pw).render_as_string(hide_password=False)
    check(True, "database created, migrations applied to head, app role is the least-privilege uptimetrace_app")

    engine = make_engine(app_url, connect_timeout=3)
    app = create_app(engine, RegistryCredentialStore(make_session_factory(engine)), clock=lambda: datetime.now(UTC), verifier=verifier())
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    port = srv.servers[0].sockets[0].getsockname()[1]
    api = f"http://127.0.0.1:{port}"
    server_started = id(srv)
    say(f"API (uvicorn, real HTTP) listening on {api}")

    tmp = Path(tempfile.mkdtemp(prefix="ut-e2e-"))
    binary = build_agent(tmp)
    say(f"agent built from ./agent -> {binary.name}")
    console = httpx.Client(base_url=api, headers=bearer(), timeout=15)
    agents: list[Agent] = []

    def hosts_get(path):
        return console.get(path)

    def row_count():
        with owner.connect() as c:
            return c.execute(text("SELECT count(*) FROM telemetry_envelopes WHERE host_id = :h"), {"h": HOST}).scalar_one()

    try:
        # ---- thresholds, so the stale/offline transition is observable in seconds (also proves configurability)
        cur = console.get("/api/v1/settings/monitoring").json()
        cfg = cur["config"]
        cfg["freshness"] = {"stale_after_seconds": 10, "offline_after_seconds": 25}
        r = console.put("/api/v1/settings/monitoring", json={"config": cfg, "expected_version": cur["version"]})
        check(r.status_code == 200 and r.json()["version"] == 1, "freshness limits configured via the settings API (stale 10 s, offline 25 s)")

        # ---- 1-4 register, issue, one-time token
        r = console.post("/api/v1/registry/hosts", json={"host_id": HOST, "display_name": "E2E host"})
        check(r.status_code == 201 and r.json()["telemetry_state"] == "awaiting_first_telemetry", "host registered; state is awaiting_first_telemetry")
        check(console.get(f"/api/v1/hosts/{HOST}").status_code == 404, "a registered host that never reported does not appear healthy (no telemetry record)")
        r = console.post(f"/api/v1/registry/hosts/{HOST}/credentials", json={"agent_id": AGENT})
        cred1 = r.json()
        tok1 = cred1.get("token", "")
        check(r.status_code == 201 and tok1.startswith("utk_") and r.headers.get("cache-control") == "no-store", "credential issued; plaintext token returned once, no-store")
        listing = console.get(f"/api/v1/registry/hosts/{HOST}/credentials").text
        check(tok1 not in listing and "token_sha256" not in listing, "token is not recoverable from any later API response")

        # ---- 5-9 real agent
        a1 = Agent(binary, api, tok1, tmp, "1")
        agents.append(a1)
        got = wait_for(lambda: (lambda r: r.json() if r.status_code == 200 else None)(hosts_get(f"/api/v1/hosts/{HOST}")), 30)
        check(got is not None, "real agent authenticated and its telemetry became visible through the read API")
        if got:
            t1 = got["last_seen"]
            time.sleep(3.5)
            g2 = hosts_get(f"/api/v1/hosts/{HOST}").json()
            check(g2["last_seen"] > t1, "last_seen advances while the agent runs", f"{t1} -> {g2['last_seen']}")
            check(g2["health"]["status"] in ("healthy", "warning", "critical") and g2["reporting_status"] == "reporting",
                  "host is reporting with a real health verdict", f"status={g2['health']['status']} category={g2['health']['category']}")
            lat = g2["latest"]
            check(lat["cpu_percent"] is not None and lat["memory_percent"] is not None and lat["disk_percent_max"] is not None,
                  "cpu, memory and disk measurements are present", json.dumps({k: lat[k] for k in ("cpu_percent", "memory_percent", "disk_percent_max")}))
            s = hosts_get(f"/api/v1/hosts/{HOST}/metrics/series?metric=system.cpu.utilization&step=60").json()
            pts = [p for ser in s["series"] for p in ser["points"] if p["value"] is not None]
            check(len(pts) >= 1, "metric history is queryable", f"{len(pts)} populated points")
            reg = [h for h in console.get("/api/v1/registry/hosts").json() if h["host_id"] == HOST][0]
            check(reg["telemetry_state"] == "reporting" and reg["active_credentials"] == 1, "registry shows the host reporting with 1 active credential")
        with owner.connect() as c:
            row = c.execute(text("SELECT count(*) n, count(DISTINCT host_id) hosts, min(host_id) h, min(agent_id) a FROM telemetry_envelopes")).one()
        check(row.n >= 3 and row.hosts == 1 and row.h == HOST and row.a == AGENT, "envelopes persisted against exactly the registered host and agent",
              f"rows={row.n} host={row.h} agent={row.a}")

        # ---- 10-13 revoke while the agent is still running; same server process throughout
        before = row_count()
        r = console.delete(f"/api/v1/registry/hosts/{HOST}/credentials/{cred1['id']}")
        check(r.status_code == 200 and r.json()["revoked_at"], "credential revoked through the API")
        time.sleep(0.3)
        mark = row_count()                                   # allow one in-flight request that was authenticated before the revoke
        time.sleep(4)
        after = row_count()
        check(after == mark, "no envelope is accepted after revocation (row count frozen while the agent keeps sending)", f"before={before} at-revoke={mark} +4s={after}")
        direct = httpx.post(f"{api}/api/v1/telemetry", headers={"Authorization": f"Bearer {tok1}", "content-type": "application/json"}, content=b"{}", timeout=10)
        check(direct.status_code == 401 and tok1 not in direct.text, "the revoked token is rejected with 401 on a direct request")
        check("HTTP 401" in a1.text() and tok1 not in a1.text(), "the agent itself logged 401 rejections and never logged its token")
        check(id(srv) == server_started and srv.started and not srv.should_exit, "API process was never restarted (same server instance)")
        a1.stop()

        # ---- 6: stale / offline transition after the agent stops
        r = console.post(f"/api/v1/registry/hosts/{HOST}/credentials", json={"agent_id": AGENT})
        tok2 = r.json()["token"]
        a2 = Agent(binary, api, tok2, tmp, "2")
        agents.append(a2)
        ok = wait_for(lambda: (lambda b: b if b["reporting_status"] == "reporting" and b["health"]["status"] != "stale" else None)(hosts_get(f"/api/v1/hosts/{HOST}").json()), 30)
        check(ok is not None, "second credential works: host is reporting again")
        a2.stop()
        t0 = time.time()
        seen: list[tuple[float, str, float]] = []

        def poll():
            b = hosts_get(f"/api/v1/hosts/{HOST}").json()
            seen.append((round(time.time() - t0, 1), b["health"]["status"], round(b["last_seen_age_seconds"], 1)))
            return b["health"]["status"] == "offline"

        wait_for(poll, 60, every=1.0)
        order = []
        for _, st, _ in seen:
            if not order or order[-1] != st:
                order.append(st)
        say("transition timeline (s after agent stopped, status, age): " + "; ".join(f"{t}s {s} age={a}" for t, s, a in seen[:: max(1, len(seen) // 8)]))
        check(order[-2:] == ["stale", "offline"] or order[-3:] == ["stale", "offline"], "stopped agent transitions to STALE and then OFFLINE", " -> ".join(order))
        final = hosts_get(f"/api/v1/hosts/{HOST}").json()
        check(final["health"]["status"] == "offline" and final["health"]["reasons"] == [] and final["reporting_status"] == "silent",
              "offline host reports no stale concerns and reporting_status=silent")

        # ---- 14: database unavailable fails closed
        r = console.post(f"/api/v1/registry/hosts/{HOST}/credentials", json={"agent_id": AGENT})
        tok3 = r.json()["token"]
        dry = subprocess.run([str(binary), "-dry-run", "-host-id", HOST, "-agent-id", AGENT], capture_output=True, text=True, timeout=60)
        envelope = json.loads(dry.stdout.splitlines()[0])

        def send(token):
            env = {**envelope, "envelope_id": str(uuid.uuid4())}
            return httpx.post(f"{api}/api/v1/telemetry", headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
                              content=json.dumps(env).encode(), timeout=30)

        check(send(tok3).status_code == 202, "valid credential is accepted while the database is up")
        container = "uptimetrace-test-pg"
        subprocess.run(["docker", "stop", container], capture_output=True, timeout=120)
        try:
            r = send(tok3)
            check(r.status_code == 503 and r.status_code != 202, "database down: ingestion answers 503, nothing is accepted", f"status={r.status_code}")
            check(httpx.get(f"{api}/health", timeout=15).status_code == 503, "/health reports degraded while the database is down")
            check(send("utk_" + "Z" * 43).status_code in (401, 503), "database down: an unknown token is never accepted")
        finally:
            subprocess.run(["docker", "start", container], capture_output=True, timeout=120)
        up = wait_for(lambda: subprocess.run(["docker", "exec", container, "pg_isready", "-U", "postgres"], capture_output=True).returncode == 0, 60, 1.0)
        check(bool(up), "database restarted")
        r = wait_for(lambda: (lambda x: x if x.status_code == 202 else None)(send(tok3)), 30, 1.0)
        check(r is not None, "after the database returns, the same credential is accepted again without restarting the API")
    finally:
        for a in agents:
            a.stop()
        srv.should_exit = True
        engine.dispose()
        owner.dispose()
        shutil.rmtree(tmp, ignore_errors=True)
        if not args.keep:
            try:   # fresh connection: the pooled admin connection may have been killed by the deliberate database stop above
                fresh = create_engine(admin_url)
                with fresh.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                    c.execute(text(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)'))
                fresh.dispose()
            except Exception as exc:  # cleanup failure must not hide the verdict; the next run drops the database first anyway
                say(f"note: could not drop {DB_NAME}: {type(exc).__name__}")
        admin.dispose()

    say(f"RESULT: {'ALL CHECKS PASSED' if not FAILS else 'FAILED: ' + '; '.join(FAILS)}")
    out = ROOT / "docs" / "evidence"
    out.mkdir(parents=True, exist_ok=True)
    (out / "m1_e2e.txt").write_text(f"run at {datetime.now(UTC).isoformat()}\n" + "\n".join(LOG) + "\n", encoding="utf-8")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
