# Implementation status

Last updated 2026-11-18 (session 1). "Verified" = executed in this session with the result stated.

## Capability matrix

| Capability | State | Evidence |
|---|---|---|
| Go agent, v1 contract, host-scoped bearer auth | Existing, working | `go test ./...` all 6 packages ok; `test_agent_contract` (real agent → real API, storage spied) passes |
| Idempotent ingestion → PostgreSQL JSONB | Existing, working | backend suite (core): pass |
| Read API: hosts, health, latest, series | Existing, working | `test_monitoring` pass |
| Health: healthy/warning/critical/offline/unknown, missing ≠ 0 | Existing, working; no distinct `stale`; thresholds fixed | `test_monitoring::test_missing_metrics_are_unknown…` |
| Frontend: Overview, Hosts, Host detail, Metrics explorer | Existing, working | vitest 56/56 |
| **Host registry + registry-issued credentials (M1)** | **New** | `tests/test_registry.py` (13 tests) pass |
| **Immediate credential revocation (M1)** | **New** | test: same token → 202 then 401 on next request, no restart |
| **Audit events for registry mutations (M1)** | **New** | test: 3 actions recorded, no token/hash in detail |
| **MCP read-only server (3 tools), opt-in** | **New**, in-process tested only | `tests/test_mcp.py` 23 tests with the official SDK client; see MCP_INTEGRATION.md |
| **MCP token lifecycle (issue/scope/expire/revoke)** | **New** | tests: 401 for missing/bad/expired/revoked, scope denial, DB-failure fails closed |
| **MCP cross-workspace isolation** | **New**, application-level | tests: A/B listings disjoint, B/orphan/missing ids give identical errors, no workspace argument |
| Alerts / incidents | Absent | — |
| Failure Readiness | Absent | — |
| OpenTelemetry / Jaeger | Absent | — |
| Docker Compose deployment | Absent | only the throwaway test Postgres container exists |
| Workspace isolation | Absent (single `default` workspace) | see KNOWN_LIMITATIONS |
| ML evaluation (held-out IID/OOD) | Not re-run; missing-as-zero defect confirmed | M5 |

## Changes this session

Migration **0003** (`workspaces`, `hosts`, `agent_credentials`, `audit_events`; grants + RLS).
New: `backend/app/registry/{__init__,store,service,router}.py`, `backend/tests/test_registry.py`, `docs/*.md` (5 files).
Modified: `backend/app/main.py` (registry router, threadpool auth, CORS adds POST/DELETE + Content-Type), `backend/app/asgi.py` (registry store; credentials.json now optional), `backend/app/auth.py` (`parse_bearer` alias), `backend/app/models.py` (4 models), `backend/tests/test_agent_contract.py` (racy uniqueness assertion, D-008), `backend/tests/test_clerk_auth.py` (CORS test updated to the new methods).
Environment: recreated `uptimetrace-test-pg` bound to 127.0.0.1 (was all interfaces).

## Tests executed (real results)

| Suite | Result |
|---|---|
| Go agent `go test ./...` | 6/6 packages ok |
| Frontend `npm test` | 56 passed (10 files) |
| Backend, non-ML (`--ignore m31..m34`) before registry fixes | 242 passed, 3 failed (1 racy agent test fixed earlier; 2 registry test bugs + 1 CORS test update, all fixed) |
| Backend affected files after fixes (registry, clerk, monitoring, auth, migrations) | 98 passed |
| Backend m31–m34 + simulator | 296 passed in 23m40s (run after all M1 changes). Note: `test_simulator.py` does use the test DB. |

Not re-run as a single combined suite after the final fixes (the full run takes ~9 min); the 98-test rerun covers every file touched.

## MCP session results

Migrations 0004; new `backend/app/mcp/*`, `tests/test_mcp.py`, `docs/MCP_INTEGRATION.md`, `docs/SECURITY.md`; modified `main.py`, `asgi.py`, `config.py`, `models.py`, `monitoring/{service,router}.py`, `pyproject.toml` (adds `mcp>=1.28,<2`).
Full non-ML backend suite after all changes: **268 passed in 12m43s** (includes 23 MCP tests and the series refactor). Simulator/ML suites were not re-run after the MCP changes (they do not import the changed modules, but this is untested).

## Not verified

* Real Go agent → real API → real PostgreSQL with a registry-issued credential (the registry test uses HTTP posts, not the agent binary).
* Migration 0003 against Supabase (applied only to the disposable DB); downgrade path untested.
* MCP against any real third-party client, over TLS, or with a hosted connector; MCP bare `/mcp` path and bad-Host status codes.
* Frontend has no registry or token UI yet — registry is API-only.
* No type checker / linter run (mypy, ruff, tsc strict) this session; no dependency audit; no container build.
* Linux agent collection on a real Linux host.

## Next

1. Frontend "Hosts & agents" page (register → token shown once → revoke).
2. `stale` state + configurable thresholds; show awaiting-first-telemetry hosts.
3. Scripted end-to-end with the real agent and disposable DB; Compose file for api + frontend + postgres.
4. M2 alert engine (dedup via partial unique index).
