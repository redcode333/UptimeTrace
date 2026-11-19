"""End-to-end: a real alert is opened, investigated, recovered, recorded, and isolated from another workspace.

Same isolated stack as run_workflow.py: disposable PostgreSQL (database `uptimetrace_e2e`, must be fresh), real migrations,
the production app factory (scripts/e2e/app.py) on 127.0.0.1, real signed ingestion, real evaluator sweep, real clock.
No mocks: telemetry is sent through the ingestion API, and every assertion reads the API or the database.

Environment:
  E2E_OWNER_URL   postgresql://postgres:<pw>@127.0.0.1:<port>/uptimetrace_e2e
  E2E_PORT        optional, default 8766
Writes docs/evidence/investigation_workflow.json (no tokens, no passwords).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
EVIDENCE = REPO / "docs" / "evidence" / "investigation_workflow.json"
PORT = int(os.environ.get("E2E_PORT", "8766"))
BASE = f"http://127.0.0.1:{PORT}"
HOST = "inv-host-01"
CPU = "system.cpu.utilization"
MEM = "system.memory.utilization"
SCRATCH = Path(os.environ.get("E2E_SCRATCH", Path(os.environ.get("TEMP", ".")) / "uptimetrace-investigation-e2e"))

sys.path.insert(0, str(BACKEND))
from tests.clerk_support import mint  # noqa: E402

steps: list[dict] = []
procs: list[subprocess.Popen] = []


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def record(name: str, ok: bool, **detail) -> None:
    steps.append({"step": name, "ok": bool(ok), "at": iso(now()), **detail})
    print(("PASS " if ok else "FAIL ") + name + (("  " + json.dumps(detail, default=str)[:400]) if detail else ""), flush=True)
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


def envelope(metrics: list[dict], when: dt.datetime, agent_id: str) -> dict:
    ms = [{"name": n, "kind": "gauge", "value": v, "unit": "percent", "timestamp": iso(when - dt.timedelta(milliseconds=500))}
          for n, v in metrics]
    return {"schema_version": "1.0", "envelope_id": str(uuid.uuid4()), "agent_id": agent_id, "host_id": HOST,
            "hostname": HOST, "timestamp": iso(when), "metrics": ms}


def main() -> int:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # one signing key, shared with the API child process
    os.environ["E2E_CLERK_PRIVATE_KEY_PEM"] = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    owner_url = os.environ["E2E_OWNER_URL"]
    if urlsplit(owner_url).path.strip("/") != "uptimetrace_e2e":
        raise SystemExit("E2E_OWNER_URL must point at the database uptimetrace_e2e")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    eng = sa.create_engine(owner_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with eng.connect() as c:
        if c.execute(sa.text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")).scalar_one():
            raise SystemExit("uptimetrace_e2e is not empty; refusing to run")

    app_pw = secrets.token_urlsafe(24)
    host_port = urlsplit(owner_url).port
    app_url = urlunsplit(("postgresql", f"uptimetrace_app:{app_pw}@{urlsplit(owner_url).hostname}:{host_port}", "/uptimetrace_e2e", "", ""))
    env = {**os.environ, "DATABASE_URL": owner_url, "MIGRATION_DATABASE_URL": owner_url, "PYTHONUNBUFFERED": "1"}

    mig = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, capture_output=True, text=True)
    record("migrations applied to a fresh database (alembic upgrade head)", mig.returncode == 0, returncode=mig.returncode)
    head = eng.connect().execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
    record("schema head is 0007 (investigation evidence and records)", head == "0007", head=head)
    with eng.begin() as c:
        c.execute(sa.text(f"ALTER ROLE uptimetrace_app LOGIN PASSWORD '{app_pw}'"))

    api_log = open(SCRATCH / "api.log", "w", encoding="utf-8")
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "scripts.e2e.app:app", "--host", "127.0.0.1", "--port", str(PORT)],
                           cwd=REPO, env={**env, "E2E_APP_DATABASE_URL": app_url}, stdout=api_log, stderr=subprocess.STDOUT)
    procs.append(api)

    def health():
        try:
            return httpx.get(BASE + "/health", timeout=3).json().get("status") == "ok"
        except Exception:
            return False
    wait_until(health, 60, "API health")
    record("real API started (production app factory, runtime role uptimetrace_app)", True, base=BASE)

    H = {"Authorization": f"Bearer {mint(exp_in=3600)}"}
    http = httpx.Client(base_url=BASE, timeout=10)

    r = http.post("/api/v1/registry/hosts", headers=H, json={"host_id": HOST, "kind": "live"})
    record("host registered", r.status_code == 201, status=r.status_code)
    r = http.post(f"/api/v1/registry/hosts/{HOST}/credentials", headers=H, json={})
    record("credential issued (shown once)", r.status_code == 201, status=r.status_code)
    token, agent_id = r.json()["token"], r.json()["agent_id"]
    AUTH = {"Authorization": f"Bearer {token}"}

    def send(metrics):
        resp = http.post("/api/v1/telemetry", headers=AUTH, json=envelope(metrics, now(), agent_id))
        if resp.status_code != 202:
            raise SystemExit(f"ingest refused: {resp.status_code} {resp.text[:200]}")

    # 1. normal telemetry first, so the trigger has telemetry before it
    for _ in range(3):
        send([(CPU, 22.0), (MEM, 40.0)])
        time.sleep(3)
    record("normal telemetry ingested before the breach (3 envelopes, CPU 22%, memory 40%)", True)

    # 2. a real breach through the signed ingestion path
    send([(CPU, 97.0), (MEM, 90.0)])
    trigger_at = now()
    cpu = wait_until(lambda: next((a for a in http.get("/api/v1/alerts?status=active", headers=H).json()["alerts"]
                                   if a["condition_key"] == CPU + "|{}"), None), 30, "CPU breach alert")
    record("breach opened a persistent critical alert", cpu["severity"] == "critical" and cpu["state"] == "open",
           alert_id=cpu["id"], value=cpu["value"], threshold=cpu["threshold"])

    # 3. the investigation: exact trigger, telemetry before and after, related alert
    time.sleep(4)
    send([(CPU, 98.0), (MEM, 91.0)])  # the breach continues: must not create a duplicate
    time.sleep(3)
    actives = [a for a in http.get("/api/v1/alerts?status=active", headers=H).json()["alerts"] if a["condition_key"] == CPU + "|{}"]
    record("the continuing breach did not duplicate the alert", len(actives) == 1, active_cpu_alerts=len(actives))

    inv = http.get(f"/api/v1/alerts/{cpu['id']}/investigation", params={"window_minutes": 30}, headers=H)
    record("investigation view opened", inv.status_code == 200, status=inv.status_code)
    v = inv.json()
    t = v["trigger"]
    record("exact triggering rule, metric, value and threshold retrieved",
           t["rule"] == "threshold" and t["metric"] == CPU and t["trigger_value"] == 97.0 and t["threshold"] == 95.0
           and t["comparison"] == ">=" and t["trigger_observed_at"] is not None and t["unavailable"] == [],
           rule=t["rule"], metric=t["metric"], value=t["trigger_value"], threshold=t["threshold"], comparison=t["comparison"],
           trigger_observed_at=t["trigger_observed_at"])
    before_trigger = [p for s in v["series"] if s["window"] == "around_trigger" for p in s["points"]
                      if p["value"] is not None and p["t"] < t["first_fired_at"]]
    after_trigger = [p for s in v["series"] if s["window"] == "around_trigger" for p in s["points"]
                     if p["value"] is not None and p["t"] >= t["first_fired_at"]]
    record("telemetry before and after the trigger is present in the investigation",
           len(before_trigger) > 0 and len(after_trigger) > 0,
           populated_buckets_before=len(before_trigger), populated_buckets_after=len(after_trigger),
           window_start=v["series"][0]["start"], window_end=v["series"][0]["end"])
    record("related alert on the same host is listed with its correlation note",
           v["related"]["total"] >= 1 and any(r["metric"] == MEM for r in v["related"]["items"]) and "correlation" in v["related"]["note"],
           related_total=v["related"]["total"], related=[r["metric"] for r in v["related"]["items"]])
    tl = [e["event"] for e in v["events"]]
    record("lifecycle events for the alert are present", tl == ["opened"], events=tl)
    record("host health is shown for the affected host", v["host"]["known"] and v["host"]["current"] is not None,
           health=v["host"]["current"]["health"]["status"] if v["host"]["current"] else None)

    # 4. acknowledge, recover, and check the recovery evidence
    r = http.post(f"/api/v1/alerts/{cpu['id']}/acknowledge", headers=H)
    record("alert acknowledged by the operator", r.status_code == 200 and r.json()["state"] == "acknowledged")
    for _ in range(3):
        send([(CPU, 20.0), (MEM, 40.0)])
        time.sleep(3)
    rec_alert = wait_until(lambda: (lambda a: a if a["state"] == "resolved" else None)(http.get(f"/api/v1/alerts/{cpu['id']}", headers=H).json()),
                           30, "recovery of the CPU alert")
    record("normal telemetry resolved the alert by recovery (not the operator)", rec_alert["resolution"] == "recovered" and rec_alert["resolved_by"] == "system")

    v2 = http.get(f"/api/v1/alerts/{cpu['id']}/investigation", params={"window_minutes": 30}, headers=H).json()
    rv = v2["recovery"]
    record("recovery evidence: recovered value and the time it was observed",
           rv["recovered"] is True and rv["value"] == 20.0 and rv["observed_at"] is not None, recovery=rv)
    record("lifecycle timeline: opened, acknowledged, recovered",
           [e["event"] for e in v2["events"]] == ["opened", "acknowledged", "recovered"], events=[e["event"] for e in v2["events"]])
    record("telemetry around the recovery sample is included",
           any(s["window"] == "around_recovery" and any(p["value"] is not None for p in s["points"]) for s in v2["series"]),
           windows=[s["window"] for s in v2["series"]])
    record("trigger evidence is unchanged after recovery (never overwritten)",
           v2["trigger"]["trigger_value"] == 97.0 and v2["trigger"]["trigger_observed_at"] == t["trigger_observed_at"])

    # 5. the persisted record: created once, reproducible, same after a refresh
    c1 = http.post(f"/api/v1/alerts/{cpu['id']}/investigation/report", headers=H)
    record("investigation record created", c1.status_code == 201 and c1.json()["verified"] is True, status=c1.status_code,
           digest=c1.json()["evidence_digest"][:16], verified=c1.json()["verified"])
    fresh = httpx.Client(base_url=BASE, timeout=10)  # a new client: a refresh, not the same session
    g = fresh.get(f"/api/v1/alerts/{cpu['id']}/investigation/report", headers=H).json()
    record("record remains available after refresh, unchanged, and reproduces from its evidence",
           g["id"] == c1.json()["id"] and g["evidence_digest"] == c1.json()["evidence_digest"] and g["verified"] is True,
           record_id=g["id"], digest=g["evidence_digest"][:16])
    c2 = http.post(f"/api/v1/alerts/{cpu['id']}/investigation/report", headers=H)
    record("a second creation returns the stored record, not a new one", c2.status_code == 200 and c2.json()["id"] == g["id"])
    facts = g["body"]["facts"]
    concl = g["body"]["conclusions"]
    record("record separates facts, conclusions and not-established items",
           bool(facts) and bool(concl) and bool(g["body"]["not_established"]),
           facts=len(facts), conclusions=len(concl), not_established=len(g["body"]["not_established"]))
    record("record claims no root cause",
           any("Root cause is not established" in n for n in g["body"]["not_established"]) and not any("root cause" in c["text"].lower() for c in concl))

    # 6. workspace isolation with a real second workspace and alert
    with eng.begin() as c:
        wid = c.execute(sa.text("INSERT INTO workspaces (slug, name) VALUES ('other-tenant', 'Other tenant') RETURNING id")).scalar_one()
        hpk = c.execute(sa.text("INSERT INTO hosts (workspace_id, host_id, kind, created_by) VALUES (:w, 'other-host', 'live', 'e2e') RETURNING id"),
                        {"w": wid}).scalar_one()
        foreign = str(c.execute(sa.text(
            "INSERT INTO alerts (workspace_id, host_pk, host_id, condition_key, rule, metric, labels, severity, state, value, threshold, "
            "trigger_value, trigger_threshold, comparison, trigger_observed_at, last_evaluated_at) VALUES (:w, :h, 'other-host', "
            "'system.cpu.utilization|{}', 'threshold', 'system.cpu.utilization', '{}', 'critical', 'open', 99, 95, 99, 95, '>=', now(), now()) "
            "RETURNING id"), {"w": wid, "h": hpk}).scalar_one())
    fi = http.get(f"/api/v1/alerts/{foreign}/investigation", headers=H)
    fr = http.post(f"/api/v1/alerts/{foreign}/investigation/report", headers=H)
    fg = http.get(f"/api/v1/alerts/{foreign}/investigation/report", headers=H)
    record("another workspace's alert: investigation, record creation and record read are all refused",
           fi.status_code == 404 and fr.status_code == 404 and fg.status_code == 404,
           investigation=fi.status_code, create=fr.status_code, read=fg.status_code)
    listed = http.get("/api/v1/alerts?status=all", headers=H).json()["alerts"]
    record("the other workspace's alert is not in this workspace's alert list", all(a["id"] != foreign for a in listed))
    with eng.connect() as c:
        leaked = c.execute(sa.text("SELECT count(*) FROM investigation_reports WHERE alert_id = :i"), {"i": foreign}).scalar_one()
    record("no record was created for the other workspace's alert", leaked == 0, records=leaked)

    # 7. database read-back (owner role)
    with eng.connect() as c:
        row = c.execute(sa.text("SELECT trigger_value, trigger_threshold, comparison, trigger_observed_at, recovery_value, recovery_observed_at, "
                                "resolution FROM alerts WHERE id = :i"), {"i": cpu["id"]}).mappings().one()
        rows = c.execute(sa.text("SELECT count(*) FROM investigation_reports WHERE alert_id = :i"), {"i": cpu["id"]}).scalar_one()
    record("database read-back: trigger and recovery evidence persisted on the alert row",
           row["trigger_value"] == 97.0 and row["comparison"] == ">=" and row["recovery_value"] == 20.0 and row["recovery_observed_at"] is not None,
           row={k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()})
    record("database read-back: exactly one investigation record for the alert", rows == 1, records=rows)

    http.close()
    fresh.close()
    api.terminate()
    api.wait(timeout=15)
    summary = {"ok": all(s["ok"] for s in steps), "steps": len(steps), "finished_at": iso(now()),
               "environment": {"database": "disposable PostgreSQL (uptimetrace_e2e, fresh)", "api": "production app factory, real migrations, runtime role",
                               "telemetry": "signed ingestion through POST /api/v1/telemetry; no mocks",
                               "clerk": "local test key set (no Clerk tenant)", "clock": "real clock; sweep every 2 s",
                               "monitoring": "production default thresholds; freshness defaults 90 s / 300 s, not exercised here"},
               "not_covered": ["browser click-through of the investigation page (no Clerk session in this environment)",
                               "the real Go agent is not used in this script (telemetry is signed by the same credential path)"],
               "steps_detail": steps}
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(summary, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"\nINVESTIGATION E2E RESULT: {'PASS' if summary['ok'] else 'FAIL'} ({len(steps)} steps)  evidence: {EVIDENCE}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except SystemExit as exc:
        print(f"\nINVESTIGATION E2E RESULT: FAIL: {exc}", flush=True)
        code = 1
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
    raise SystemExit(code)
