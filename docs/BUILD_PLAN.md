# UptimeTrace — Build Plan

Source of truth for sequencing. Status lives in `IMPLEMENTATION_STATUS.md`; reasons live in `DECISIONS.md`.
Written 2026-11-18 from an inspection of the actual repository (not from the mandate's assumptions).

## Baseline facts found in M0 (verified, not assumed)

| Area | Finding |
|---|---|
| VCS | **Not a git repository.** No history, no branches. `git init` + a baseline commit is the first safe step once the owner agrees (see Blockers). |
| Python | 3.12.4 (Anaconda). fastapi 0.124, SQLAlchemy 2.0.30, alembic 1.20, psycopg 3.3, pytest 8.3. `xgboost 3.4.1`, `scikit-learn 1.4.2`, `numpy 1.26`, `pyarrow 21`, `pandas 2.2` installed globally but **not declared** in `pyproject.toml` (`ml` extra lists numpy/pyarrow/psutil only). |
| Go / Node / Docker | go 1.27.4, node 24.11, Docker 29.9 (Desktop must be started manually). |
| Dev database | Supabase (remote). **Never used by tests** — `tests/conftest.py` refuses any non-loopback `TEST_DATABASE_URL` or one equal to `DATABASE_URL`. |
| Test database | Throwaway container `uptimetrace-test-pg` (postgres:16-alpine) on `127.0.0.1:55432`. The pre-existing container published on all interfaces; recreated loopback-only in M0. |
| Destructive commands | `tests/conftest.py::engine` runs `DROP SCHEMA public CASCADE` on `TEST_DATABASE_URL`; `clean_table` truncates `telemetry_envelopes`; `tests/test_registry.py` truncates registry tables. All guarded by the loopback check. `python -m app.provision_app_role` rewrites `.env` (dev only). |
| Secrets on disk | `backend/.env`, `backend/credentials.json` (hashes), `backend/.env.bak-*`, `frontend/.env`. All git-ignored by pattern; they must never be committed or echoed. |

## Milestones

| # | Goal | Acceptance |
|---|---|---|
| M0 | Baseline, safety, plan | Test DB confirmed disposable; all suites run; findings recorded. **Done.** |
| M1 | Trustworthy host monitoring | Register host → issue credential → run agent → see metrics/history/freshness → stop agent → correct stale/offline state → revoke credential and ingestion is refused on the next request. |
| M2 | Alerts + incidents | One alert per breach (dedup), ack persisted, recovery resolves, incident from alert with evidence + timeline. |
| M3 | Failure Readiness | Isolated memory-pressure run in a disposable container; real alert transition via the *same* evaluator; persisted report with detection delay. |
| M4 | OpenTelemetry | Demo FastAPI service → private OTLP collector → Jaeger; linked spans; no secrets in spans; v1 telemetry contract untouched. |
| M5 | ML evaluation | Reproducible train/val/test (IID + OOD) with train-only fitting and validation-only thresholds; missing-as-zero fixed. |
| M6 | Workspaces + hardening | Membership/roles, RLS by workspace, cross-tenant tests, retention, backup/restore proof. |

Ordering note: workspace identity is introduced in M1 (every new table has `workspace_id`) so M2–M3 records need no redesign.

## MCP track (added 2026-11-18, see `MCP_INTEGRATION.md`)

Read-only MCP delivered early for the three host tools because their backends exist (M1). Remaining MCP work is gated by the product milestones:
M2 → alert/incident tools; M3 → experiment tools; M4 → service/trace tools (needs a verified service↔host association); M6 → **must land before any independent-customer exposure** (membership, RLS, tenant-isolated console, OAuth decision).
Next MCP steps in order: console UI for tokens → real-client verification over TLS → OAuth/discovery decision → distributed rate limit.

## M1 breakdown

1. **Done** — registry tables (`workspaces`, `hosts`, `agent_credentials`, `audit_events`), migration 0003, least-privilege grants, RLS.
2. **Done** — `RegistryCredentialStore`: DB-backed agent auth, immediate revocation, fail-closed on DB error; legacy `credentials.json` still honoured.
3. **Done** — `/api/v1/registry/*` endpoints with audit events; tests.
4. Next — frontend Settings → "Hosts & agents" (register, show token once, revoke) against the registry API.
5. Next — health semantics: split `offline` into `stale` (late) and `offline`, configurable per-workspace thresholds (table + API), identical labels in frontend.
6. Next — end-to-end proof with the real Go agent against a local API + disposable DB (script in `scripts/`), including stop → stale/offline and revoke.
7. Next — agent queue durability (disk spool) — justified: currently an in-memory buffer loses data on crash.

## M2 / M3 sequence (design decided, not started)

* Alert evaluator is a pure function `evaluate(rule, samples, now) -> Condition` plus a lifecycle service that upserts `alerts` keyed by `(workspace_id, rule_id, host_id, fingerprint)` with a partial unique index on non-RESOLVED rows (dedup is a database guarantee, not an application check). It runs in a worker loop and is invoked inline after ingestion for low latency; the same function is called by the experiment runner (no second engine).
* Experiment runner executes only **versioned, built-in scenario definitions** inside a disposable container with cgroup limits; no user shell strings. The experiment target is a dedicated synthetic host registered with its own credential, so experiment telemetry is separable from customer telemetry by `hosts.kind`.

## Blockers needing the owner (none block M1 code)

1. Approval to `git init` this directory and create a baseline commit (not done unilaterally: no history exists, and OneDrive sync is active on the folder).
2. Clerk/Supabase credentials for the hosted environment remain the owner's; local work uses the disposable database and a locally minted-JWT test harness.
