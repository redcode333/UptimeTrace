# Implementation status

Last updated 2026-11-19. "Verified" = executed with the result stated below. Evidence files are under `docs/evidence/`.

## Capability matrix

| Capability | State | Evidence |
|---|---|---|
| Go agent, v1 contract, host-scoped bearer auth | Working | `go test ./...` in `agent/`: 6/6 packages ok; E2E run below |
| Idempotent ingestion → PostgreSQL JSONB | Working | E2E checkpoint 3; `test_agent_contract` |
| Read API: hosts, health, latest, series | Working | `test_monitoring`; E2E checkpoint 4 |
| Health: healthy/warning/critical/offline/unknown, missing ≠ 0 | Working | `test_health_states`; E2E checkpoints 4, 9, 10 |
| Stale state (reporting → stale → offline) | Working, configurable per workspace | E2E checkpoint 10 (10 s / 20 s windows) |
| Workspace monitoring settings (thresholds, freshness) | Working via API; no console UI for them | E2E checkpoint 3 (`PUT /api/v1/settings/monitoring`) |
| Host registry + registry-issued credentials | Working | `test_registry`; E2E checkpoints 2, 11, 12 |
| Immediate credential revocation | Working | E2E checkpoint 13 (401 on the next request, agent keeps running) |
| Persistent threshold and host_offline alerts (dedup, acknowledge, recover, timeline) | Working via API | `test_alerts`; E2E checkpoints 5–9, 10 |
| Frontend: Overview, Hosts, Host detail, Metrics explorer | Working | vitest 88/88 (13 files, full run) |
| **Alert investigation: trigger and recovery evidence persisted** | **Working** (migration 0007) | `test_investigation` (trigger immutability, unavailable legacy evidence); investigation E2E |
| **Investigation view API (bounded telemetry, lifecycle, related alerts, recovery)** | **Working via API** | `test_investigation`; `scripts/e2e/investigation_workflow.py` 30/30 |
| **Investigation record (facts, conclusions, not-established, digest, re-derivation)** | **Working** | `test_investigation` (create once, reproduces, tamper detection, app role cannot update/delete); E2E |
| **Investigation page in the console (from Alerts)** | **Built, unit-tested; not browser-verified** | `InvestigationPage.test.tsx` 11 tests; `tsc` clean |
| Alert and agent screens in the console | Built in source; not browser-verified | no browser run in this session |
| MCP read-only server (3 tools), opt-in | In-process tested only | `test_mcp`; see `docs/MCP_INTEGRATION.md` |
| Incidents, Failure Readiness, OpenTelemetry/Jaeger | Absent | — |
| Docker Compose deployment | Absent | only throwaway test Postgres containers exist |
| Multi-workspace isolation | Absent (single `default` workspace) | see `KNOWN_LIMITATIONS.md` |
| ML evaluation (held-out IID/OOD) | Not re-run; not part of this slice | `docs/ML_EVALUATION_PROTOCOL.md` |

## End-to-end workflow (real agent → real API → PostgreSQL → alerts)

Script: `scripts/e2e/run_workflow.py`. Result of the final run: **PASS, 34/34 checks**, evidence in `docs/evidence/e2e_workflow.json` (`finished_at` 2026-11-19T06:31:23Z).

Environment: disposable PostgreSQL 16 container `ut-e2e-pg` (127.0.0.1:15432), database `uptimetrace_e2e` (fresh, migrations applied by the run); the production app factory via `scripts/e2e/app.py` on 127.0.0.1:8765; the Go agent built from `agent/` and run as a child process; runtime role `uptimetrace_app`. Workspace windows were set to stale 10 s / offline 20 s through the settings API (production defaults are 90 s / 300 s; the script does not change the logic).

Checkpoints from the task brief, and what the run showed:

1. Workspace and host registration — PASS (`e2e-agent-01`, `e2e-controlled-01`, HTTP 201).
2. Credential issuance and authentication — PASS (201, `Cache-Control: no-store`, token absent from the listing; agent authenticates with `UPTIMETRACE_TOKEN`).
3. Real telemetry ingestion and persistence — PASS (rows read back with the owner role: 6 envelopes for the agent host, 5 for the controlled host).
4. Host online and current metrics — PASS (`reporting`, CPU 9.4 %, memory 88.73 %, last seen 0.1 s).
5. Threshold breach creates a persistent alert — PASS (CPU 97 % → critical alert, value 97, threshold 95). The breach is on the controlled host. The agent host's own alerts were from its real disk and memory readings: disk `C:` critical (96.06 % ≥ 95), disk `D:` warning (93.83 % ≥ 90), memory warning (89.11 % ≥ 85); these were still open at the end of the run.
6. Repeated evaluations do not duplicate — PASS (second breach sent, 1 active alert).
7. Acknowledge and persist — PASS (`acknowledged_by` = the allowlisted user; timeline has `acknowledged`).
8. Recovery resolves by the system — PASS (`resolution: recovered`, `resolved_by: system`, timeline `opened → acknowledged → recovered`).
9. Stop telemetry → stale → offline, alert raised and cleared — PASS (controlled host: stale at 15–19 s, offline at 20 s, never `healthy` while silent; `host_offline` alert opened and recovered when telemetry resumed). The run then sent no further telemetry for the controlled host, so a second `host_offline` alert is open at the end of the run (`opened_at` 06:30:59Z). This is the expected state, not a defect.
10. Stop the real agent → stale → offline — PASS (stale at 13–19 s, offline at 20 s, never healthy; `host_offline` alert raised).
11. Restart the agent → reconnection and fresh telemetry — PASS (reporting again; `host_offline` for the agent host resolved).
12. Credential revocation and rejection — PASS (401 on the next request; agent kept running with no new rows persisted; rejection logged).

Database read-back at the end: 11 envelopes across the two hosts; alert rows and timeline events were written.

## Alert investigation (second vertical slice)

Design and API: `docs/INVESTIGATION.md`. Migration **0007** (`backend/alembic/versions/0007_investigation.py`): nullable trigger and
recovery evidence columns on `alerts`, backfilled only from values existing alert events already recorded; the append-only table
`investigation_reports`. The application role has no UPDATE grant on trigger columns and no UPDATE or DELETE on reports.

Evaluation changes (behaviour unchanged, evidence added): `Reason` and `Condition` carry the observation time; the evaluator writes
trigger evidence on open and recovery evidence on system recovery. Existing alert responses are unchanged; investigation data is
served from `/api/v1/alerts/{id}/investigation` and `/investigation/report`.

Real E2E, `scripts/e2e/investigation_workflow.py`, run on a fresh disposable `uptimetrace_e2e` with migrations to head 0007, the production
app factory, and signed ingestion (real clock; 3 normal envelopes, a CPU 97 % and memory 90 % breach, a continued breach, acknowledgement,
recovery to CPU 20 %, then the record and a cross-workspace attempt): **PASS, 30/30**, evidence `docs/evidence/investigation_workflow.json`.
Checkpoints from the brief:

1. Real alert triggered: CPU 97 % opened one critical alert; the continued breach did not duplicate it.
2. Investigation opened (200).
3. Exact rule, metric, value, threshold and comparison retrieved (`threshold`, `system.cpu.utilization`, 97.0, 95.0, `>=`), with the sample time.
4. Telemetry before and after the trigger present (1 populated bucket each side: the run's telemetry spanned seconds, so buckets are coarse).
5. Related alert on the same host (memory 90 %) listed, with the correlation note.
6. Recovery by telemetry, not the operator: recovered value 20.0 at its observed time; timeline `opened → acknowledged → recovered`.
7. Record created (201, verified), then after a new client's refresh the same record and digest (200 on re-create, same id).
8. Workspace isolation: a real second workspace with its own alert gets 404 on investigation, record creation and record read, is absent from
   the alert list, and has no record.

Tests:

| Suite | Command | Result |
|---|---|---|
| Backend, investigation | `cd backend && python -m pytest tests/test_investigation.py -q` | 12 passed |
| Backend, affected set (final) | `cd backend && python -m pytest tests/test_clerk_auth.py tests/test_alerts.py tests/test_health_states.py tests/test_registry.py tests/test_monitoring.py tests/test_mcp.py tests/test_migration_roundtrip.py tests/test_investigation.py -q` | 156 passed |
| Frontend, investigation and alerts | `cd frontend && npx vitest run src/features/investigation src/features/alerts` | 23 passed |
| Frontend, full | `cd frontend && npx vitest run` | 88 passed (13 files), exit 0 |
| Type check | `cd frontend && npx tsc --noEmit -p .` | exit 0 |
| Go agent (unchanged code) | `cd agent && go test ./...` | 6/6 packages ok (cached) |

Fixed during this slice (test-side, not product): the test clock started at T0, so a sample from 20 minutes earlier was correctly judged
silent and raised a `host_offline` alert; the test now starts the clock at its first sample. The migration round-trip test had the head
hard-coded as 0006; it now expects 0007. Both changes were the test's own assumptions, and no assertion was weakened.

## Command Center (frontend implementation)

Implemented from `docs/FRONTEND_ARCHITECTURE.md` revision 3 in `frontend/src/features/overview/` (OverviewPage, ActiveAlertsPanel, RecentTransitionsPanel, ConnectedPanel) with pure logic in `frontend/src/lib/commandCenter.ts` and `frontend/src/lib/alertTransitions.ts`, and tests alongside them. Status: built and unit-tested; **not browser-verified** (no authenticated browser session is available).

* CC-1 overall state (exact facets), CC-2 LIVE-only line (respects scope; source labels are derived from host IDs and say so), CC-3 data sources (not presented as stored provenance), CC-4 attention (exact heading count; partial ranking notice above 100), CC-5 active alerts (exact or partial counts by the documented rules; acknowledge refreshes all alert queries), CC-6 transitions (four documented kinds only; RECOVERED and RESOLVED MANUALLY distinguished; a capped list never claims an empty window), CC-7 utilisation (colour and tallies from the configured thresholds; missing values counted separately).
* Warning colour is the approved pale violet `#e9b8ff`; critical glyph is a solid square; SYNTHETIC badge is neutral ink with a dashed border; error banners use the critical colour; notices use a neutral border.
* Alert list now accepts an optional `limit` (default unchanged at 100 for existing callers).
* Not built: any backend change. Source-filtered alert counts use an all-sources host list only to recognise each alert's source; that request is the one unscoped host request on the page.

Tests for this work: `lib/commandCenter.test.ts` and `lib/alertTransitions.test.ts` (32 pure tests), and `features/overview/OverviewPage.test.tsx` (29 tests in total, 21 new).

Results (this session): `npx tsc --noEmit -p .` exit 0. `npx vitest run --no-file-parallelism`: 15 of 15 files, 141 of 141 tests passed. The default parallel `npx vitest run` was run three times with intermittent timeouts across unrelated pages on this machine (see KNOWN_LIMITATIONS); it was not used as the basis for the result above.

## Host Detail (frontend implementation)

Changes in `frontend/src/features/hosts/HostDetailPage.tsx` (same sections and layout, plus an operational-state strip and an alerts section), with pure logic in `frontend/src/lib/hostDetail.ts`.

* Operational state: health and agent reporting shown as separate facts, with last telemetry and source. The source is stated as derived from the host ID.
* Current values use the configured warning and critical limits for CPU, memory and each disk mount. Stale samples are labelled stale. Missing values are "not reported". If the thresholds cannot be loaded, values are shown without a state and the page says so.
* Alerts for this host: the alert list filtered on the server by `host_id`. Active alerts (critical first) and recently resolved alerts are shown separately, with acknowledgement and an Investigate link. Resolutions are labelled "Recovered by telemetry" or "Resolved manually by X".
* A host registered but never reported is explained using the registry, rather than shown as not found.
* API additions: the alert client accepts a `host_id` filter; a read-only client call for `GET /api/v1/settings/monitoring`. No backend change.

Results (this session): `npx tsc --noEmit -p .` exit 0; `npx vitest run --no-file-parallelism`: 16 of 16 files, 169 of 169 tests passed. Browser verification: not performed.

## Alert Policies (frontend implementation)

New page at `/settings/alerts` (nav: Settings > Alert Policies), `frontend/src/features/settings/AlertPoliciesPage.tsx`, with validation in `frontend/src/lib/monitoringSettings.ts`. It uses the existing `GET` and `PUT /api/v1/settings/monitoring` only; no backend, schema or evaluation change.

* Shows the saved CPU, memory and disk warning and critical limits, the stale and offline timeouts, the saved version, and the built-in defaults beside each value.
* Validation mirrors the backend's rules: limits are percentages with 0 < warning < critical <= 100; stale is a whole number of seconds from 10 to 3600; offline is at most 86400 and longer than stale. Invalid input cannot be submitted.
* A save is shown as successful only after the server confirms it, and then the hosts, host, alert and settings queries are refreshed. A version conflict (409) does not overwrite anything and offers a reload of the saved values.
* Nothing is saved on load, and "Fill in defaults" only edits the form.
* Explains what warning, critical, stale and offline mean, and that stale is late telemetry, not a crash.

Results (this session): `npx tsc --noEmit -p .` exit 0; `npx vitest run --no-file-parallelism`: 18 of 18 files, 202 of 202 tests passed. Browser verification: not performed.

## MCP Integrations (frontend implementation)

Page at `/settings/integrations` (nav: Settings > Integrations), `frontend/src/features/settings/IntegrationsPage.tsx`, with token rules in `frontend/src/lib/mcpTokens.ts`. Uses only the existing token endpoints under `/api/v1/registry/mcp-tokens` (list, issue, revoke). No backend change.

* Overview: read-only designation, endpoint (`<api>/mcp/`, trailing slash), Streamable HTTP transport (JSON, stateless), Bearer authentication, the three tools and their scopes, rate limit, and limitations (static tokens only; no OAuth or hosted connector discovery).
* The API does not report whether MCP is enabled, so the page states that instead of showing a status.
* Tokens: create with a name, scopes (`hosts:read`, `metrics:read` only) and expiry (1 to 90 days), validated against the backend rules. The raw token is shown once, from component state, with a secret warning and copy. It is not stored in any cache, URL or browser storage. Revocation asks for confirmation. Lists show ACTIVE, EXPIRED or REVOKED.
* Client setup: endpoint, header, a configuration shape marked as unverified against any specific client, and a verification script with the official MCP Python SDK (the client the backend tests use). Snippets contain placeholders only.

Results (this session): `npx tsc --noEmit -p .` exit 0; `npx vitest run --no-file-parallelism`: 20 of 20 files, 237 of 237 tests passed. Browser verification: not performed. No MCP client other than the official SDK was run.

## First-time onboarding (frontend implementation)

Page at `/onboarding` (`frontend/src/features/onboarding/OnboardingPage.tsx`), with pure logic in `frontend/src/lib/onboarding.ts`. Entry points: the Command Center empty state ("Set up your first host") and a "Register a host" action on the Hosts page. A workspace with hosts is not sent through setup; it can still register another host on purpose.

* Steps: register the host (host id, kind, optional display name, validated against the backend's id and kind rules); create the agent credential (shown once from component state, revocable; an existing secret is not shown again); install and configure the agent (build from `agent/` with `go build`, then environment variables; Windows verified, Linux not); verify first telemetry (polls the registry every 5 s while the panel is mounted; stops when left); review thresholds (read-only, linking to Alert Policies).
* Completion is derived from the server, not stored: connected means the registry reports the host as reporting and an envelope has been received. A host that was once connected and is now late or silent returns to verification.
* Progress survives interruption: the only URL state is the host id; the step is derived from the registry and credential list on every visit.
* No backend change. No onboarding persistence exists on the server, so none is invented.

Results (this session): `npx tsc --noEmit -p .` exit 0; `npx vitest run --no-file-parallelism`: 22 of 22 files, 279 of 279 tests passed. Browser verification: not performed.

## Infrastructure inventory (grouping foundation)

Implemented: environments, regions, clusters and services as persistent workspace rows, with hosts and services linked explicitly. Grouping is optional at every level. Migration 0008 adds the tables and a nullable `hosts.cluster_id`.

- Relationships: Environment → Region (optional), Region → Cluster (optional), Cluster → Host (at most one cluster per host), Service ↔ Host (many to many).
- API: `GET /api/v1/infrastructure` plus create, rename/move and delete for each entity, and host assign/remove, service link/unlink. Browser session required; workspace resolved on the server.
- Deleting a grouping detaches references only. Hosts, telemetry, alerts and investigations are never deleted by these calls (tested).
- Frontend: `/infrastructure`, linked from the Hosts header (not in the sidebar). Grouped and ungrouped rendering, create, rename, move, assign, remove, and delete with in-place confirmation.
- Existing hosts without metadata appear as "not in a cluster"; nothing is inferred from hostnames, IPs or host IDs.

Test results: backend infrastructure tests 23/23; full backend suite 651 passed (single run, disposable `uptimetrace_test` database); migration round-trip passes at head 0008; frontend `tsc --noEmit` clean; frontend full suite 303/303 (sequential run). Browser verification: not performed.

## Failures found and fixed in this session

1. **First run failed at step 4 with HTTP 401** (`PUT /api/v1/settings/monitoring`). Cause: the test harness generated a separate random RSA signing key in the runner process and in the API child process, so the API could not verify the test token. Fix: `backend/tests/clerk_support.py` loads the key from `E2E_CLERK_PRIVATE_KEY_PEM` when it is set (unset = unchanged behaviour), and `run_workflow.py` generates one key per run and passes it to the child. Unit tests are unaffected.
2. The failed run had already applied migrations to `uptimetrace_e2e`, so the script's empty-database guard blocked the rerun. That database was dropped and recreated; nothing else in `ut-e2e-pg` or any other container was changed.

## Test commands and results (this session)

| Suite | Command | Result |
|---|---|---|
| Go agent | `cd agent && go test ./...` | 6/6 packages ok |
| Backend, affected set | `cd backend && python -m pytest tests/test_clerk_auth.py tests/test_alerts.py tests/test_health_states.py tests/test_registry.py tests/test_monitoring.py tests/test_mcp.py tests/test_migration_roundtrip.py -q` | 144 passed |
| Frontend, full (isolated rerun, complete log) | `cd frontend && npx vitest run` | 77 passed (12 files), exit 0, 7.96 s |
| E2E | `E2E_OWNER_URL=… python scripts/e2e/run_workflow.py` | 34/34 PASS |

Test database: `TEST_DATABASE_URL` pointed at the local disposable `uptimetrace_test` database in `ut-e2e-pg` (127.0.0.1:15432). The configured `localhost:55432` target from `backend/.env` was unreachable because `uptimetrace-test-pg` has no host port published; that container was not modified.

Not run: the full backend suite (about 13 min without ML, about 24 min more with the simulator/ML files) and the ML suites. Those files do not import the alert or monitoring changes beyond what the affected set covers, but this is not proven.

## Verified vs not verified

Verified by the E2E run above: the API, ingestion, persistence, alerts, stale/offline transitions and agent reconnection with the real Go binary and PostgreSQL.

Not verified:
* The `critical` health of the agent host is explained by disk `C:` at 96.06 % (rule `system.disk.utilization`, critical 95 %). Memory 88.73 % at step 12 was a warning, not critical. The evidence JSON does not record the per-alert rule; this comes from the `alerts` table rows in the E2E database (`uptimetrace_e2e`, not committed to the repo).
* Browser click-through of the alert and agent screens. No Clerk session exists in this environment; the E2E API calls used the local test key set.
* Production stale/offline defaults are 90 s / 300 s (`backend/app/monitoring/settings.py`, `MonitoringConfig`). They were not exercised by the E2E run, which used 10 s / 20 s, stored in `monitoring_settings` for the run's workspace. The default values were confirmed in code; they were not run end to end.
* Agent disk spool: the agent buffer is in memory.
* Real Clerk tenant sign-in and allowlist.
* MCP against any real third-party client, over TLS, or hosted.
* Linux agent collection on a real Linux host.
* Type checker and linter (mypy, ruff, tsc strict) and dependency audit.

## Next

1. Browser verification of the alert, investigation and agent screens with a real session.
2. Browser verification of the infrastructure page with an authenticated session (not performed).
3. Compose file for api + frontend + postgres.
4. Incidents (not started).
