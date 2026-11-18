# MCP integration

UptimeTrace exposes a **read-only** Model Context Protocol server so customer-owned AI agents can query their workspace. It is an
interface onto the existing platform services, not a second platform: the tools call the same monitoring queries and health logic as
the console (`app/monitoring/*`), and a test asserts the MCP and console answers are identical.

**Status: implemented, tested in-process, OFF by default, not production-ready** (see "Release gates").

## Gap analysis (what existed → what MCP needed)

| Need | State before | Done now |
|---|---|---|
| Host list/health/metrics services | Existing (`app/monitoring`) | Reused; series logic extracted to `monitoring.service.load_series` so console and MCP share one implementation |
| Workspace identity | Single seeded `default` workspace; allowlist only | `workspaces` + `hosts.workspace_id` (migration 0003); **MCP tokens carry the workspace server-side** (0004) |
| Machine-client auth | Agent tokens (ingest only), Clerk sessions (browser) | Separate `utm_` MCP tokens: hashed, scoped, expiring, revocable |
| Audit | Registry mutations (0003) | Every MCP tool call, including denied ones |
| Alerts, incidents | **Absent (M2)** | → no tools |
| Experiments | **Absent (M3)** | → no tools |
| Services / traces | **Absent (M4/M5)** | → no tools |
| Console pages for deep links | Only `/hosts` and `/hosts/:hostId` exist | Links for hosts only |
| Real multi-tenant console/RLS | **Absent (M6)** | MCP-level isolation only; see limitations |

## SDK and transport

* **Official `mcp` Python SDK (FastMCP), pinned `>=1.28,<2`.** Chosen because it is the maintained reference implementation, runs inside the
  existing FastAPI process (no new service) and supplies auth middleware, session handling and spec-compliant transports. Version 2.x exists (2.3.0 on PyPI); the documentation I found says it renames the server class and changes imports, which I did not verify; migration is a tracked task, not done blind.
* **Streamable HTTP**, the SDK's remote transport, mounted at `/mcp/` (note the trailing slash) in **stateless JSON mode**: no server-side session
  state, so nothing can leak between callers and the API scales horizontally. stdio/SSE are not exposed.
* **Opt-in:** the endpoint exists only when `MCP_ENABLED=true` and `MCP_PUBLIC_URL` and `MCP_ALLOWED_HOSTS` are set (otherwise the app refuses to
  start). `MCP_ALLOWED_HOSTS` turns on the SDK's DNS-rebinding protection.

## Authentication and authorisation design

1. A console user (Clerk session, allowlisted) issues a token: `POST /api/v1/registry/mcp-tokens {name, scopes, expires_in_days}`. The plaintext
   (`utm_…`, 256 random bits) is returned **once** with `Cache-Control: no-store`; only its SHA-256 is stored. Expiry is mandatory (default 30, max 90 days).
2. The MCP client sends `Authorization: Bearer utm_…`. The SDK middleware calls our verifier, which reads the database **on every request**
   (no cache): unknown, malformed, expired and revoked tokens are indistinguishable (401). If the database is unreachable the token is **not** accepted.
3. The verified token's record supplies the **workspace id** and scopes. No tool has a workspace parameter; extra arguments are ignored and tested.
4. Scopes: `hosts:read` (`list_hosts`, `get_host_health`), `metrics:read` (`get_host_metrics`). Checked per call, server-side.
5. Visibility = hosts registered in the token's workspace and not deactivated. Telemetry has no workspace column (frozen v1 contract), so **unregistered
   hosts are invisible to MCP**. A host in another workspace and a non-existent host return the *same* error (no existence oracle).
6. Per-token rate limit: 60 calls / 60 s (in-process; with N workers the effective limit is N×).
7. Audit: each call writes `mcp.tool_called` (actor `mcp:<token name>`, tool, arguments, token id) in its own transaction *before* execution, so denied and
   failed calls are recorded. Tokens/secrets are never written.
8. Agent ingestion tokens (`utk_`), Clerk session JWTs and MCP tokens are mutually non-interchangeable (tested).

Not implemented: OAuth 2.1 authorization-server flows (dynamic client registration, authorization-code + PKCE) and RFC 9728 discovery at the origin root.
The SDK serves protected-resource metadata under the mount path only. Clients that require OAuth discovery (for example hosted connector UIs) therefore **cannot
connect yet**; clients that let you configure a static `Authorization` header can. Both statements are about the protocol requirements; **no real third-party MCP client
has been run against this server** (only the official SDK client).

## Tools (first release: 3, all backed by working services)

| Tool | Scope | Returns |
|---|---|---|
| `list_hosts(limit≤100, offset)` | hosts:read | Registered hosts with `telemetry_state`, health (`healthy/warning/critical/offline/unknown` + evidence), last seen, latest utilisation, `console_url` |
| `get_host_health(host_id)` | hosts:read | Same plus latest samples (each flagged `stale`) |
| `get_host_metrics(host_id, metric, start?, end?, step_seconds?, agg)` | metrics:read | Bounded series: ≤7 days, ≤500 points, five whitelisted metrics, counters as per-second rates, gaps as `null` |

Semantics: a host that never reported is `awaiting_first_telemetry` / `unknown`, never healthy and never zero. There is no `stale` or `degraded` health state yet (the platform has none, so the tool
does not invent one).

### Deferred tools (blocked on missing backends; deliberately not registered)

`list_active_alerts`, `get_alert_details`, `get_alert_history` (M2 alert engine) · `list_incidents`, `get_incident_details`, `get_incident_timeline` (M2) ·
`list_services`, `get_service_traces` (M4 OpenTelemetry + verified service↔host association) · `list_experiments`, `list_experiment_runs`,
`get_experiment_result` (M3 Failure Readiness). Absent tools are honest; empty-success stubs are not allowed.

### Resources and prompts

None registered. *Tools* are model-invoked and parameterised (right for bounded queries). *Resources* are application-selected context; the only candidates today (host
inventory, health overview) are already `list_hosts`, and a resource has no natural pagination, so it would add an unbounded-read surface for no gain. *Prompts* are user-invoked
templates ("investigate this alert") that need alerts to exist. Revisit resources for reports (M3) and prompts after M2.

## Dashboard deep links

`console_url` is built only for routes that exist in `frontend/src/app/App.tsx` (`/hosts/:hostId`) from `CONSOLE_BASE_URL`; when unset it is `null` — no URL is invented. Alert, incident,
experiment and trace pages do not exist, so no links to them are produced.

## Connecting a client (customer steps)

Prerequisites: an operator has enabled MCP (`MCP_ENABLED`, `MCP_PUBLIC_URL`, `MCP_ALLOWED_HOSTS`, `CONSOLE_BASE_URL`) behind TLS; hosts are registered and reporting.

1. In the console API, create a token (UI not built yet): `POST /api/v1/registry/mcp-tokens` with your Clerk session. Choose the minimum scopes and a short expiry. Copy the token now.
2. Configure your MCP client with URL `https://<api-host>/mcp/` and header `Authorization: Bearer <token>`. Example for a client using the common JSON config shape (**unverified against any specific product**):
   ```json
   { "mcpServers": { "uptimetrace": { "type": "http", "url": "https://api.example.com/mcp/", "headers": { "Authorization": "Bearer utm_…" } } } }
   ```
3. Ask: "Which hosts look unhealthy?" The agent calls `list_hosts`; follow the `console_url` for evidence.
4. Revoke: `DELETE /api/v1/registry/mcp-tokens/{id}` — effective on the next call.

Troubleshooting: 401 = unknown/expired/revoked token or missing `Authorization` header · a 4xx before authentication = Host header not in `MCP_ALLOWED_HOSTS` (exact status not tested) · tool error `host_not_found` = host is unregistered, deactivated or in
another workspace · `rate_limited` = more than 60 calls/min · use the trailing slash (`/mcp/`); behaviour of the bare `/mcp` path was not tested.

## Future write operations (design only — nothing implemented)

Candidates: acknowledge alert, create incident, add note, start an *approved* experiment, generate report, trigger an *approved* remediation workflow. Every write tool must:
(1) use a distinct `*:write` scope that is never part of a default token; (2) resolve workspace from the token and re-check object ownership; (3) validate input against closed enums
and size limits; (4) accept an `idempotency_key` stored with the audit event (replays return the first result); (5) write an audit event *before* acting and a result event after;
(6) for sensitive actions (experiments, remediation) **not execute** but create a *pending approval* that a human confirms in the console — the agent cannot self-approve;
(7) define failure behaviour (action row `failed` with reason, no partial silent state; compensating action only where the action is defined as reversible). No arbitrary
commands, SQL or infrastructure access will be exposed; remediation is limited to a registry of named, versioned workflows.

## Release gates before exposing to independent customers

1. M6 workspace membership + per-workspace RLS and a console that is itself tenant-isolated. 2. OAuth/discovery or an explicit decision to ship static tokens only. 3. Distributed rate limiting.
4. Test with real MCP clients over real TLS. 5. M2–M3 tools. Until then keep `MCP_ENABLED=false` on any shared deployment.
