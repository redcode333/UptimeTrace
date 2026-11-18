# Architecture (as implemented)

Only components that exist in the repository are listed as present. Planned items are marked **planned**.

```
 Go agent (agent/) ──HTTPS POST /api/v1/telemetry (Bearer host-scoped token, contract v1)──▶ FastAPI (backend/app)
   in-memory buffer, retry+backoff,                                                            │
   graceful drain                                                                              ├─ ingest pipeline: auth → size → content-type → JSON → schema → host scope → semantics → idempotent INSERT
                                                                                               ├─ read API  /api/v1/hosts…   (Clerk session + allowlist)
 Browser (frontend/, React+Vite+Clerk) ──Bearer Clerk JWT──▶                                   ├─ registry  /api/v1/registry… (same gate; mutations audited)
                                                                                               ├─ MCP (opt-in) /mcp/  (Bearer utm_ token)
                                                                                               └─ /health
                                                                                              PostgreSQL
                                                                                               telemetry_envelopes (JSONB, source of truth for telemetry)
                                                                                               workspaces · hosts · agent_credentials · audit_events   (migration 0003)
                                                                                               mcp_tokens                                              (migration 0004)
```

## Backend modules

| Module | Responsibility |
|---|---|
| `app/main.py` | App factory, error envelope, ingestion route, router wiring, optional MCP mount, CORS (GET/POST/DELETE, bearer header only). |
| `app/ingest/*` | Contract validation and idempotent storage (unique `envelope_id` + content hash). |
| `app/auth.py` | Credential model, scope rules (kind ↔ host prefix), legacy file store + CLI. |
| `app/registry/` | `store.py` DB credential lookup (no cache; fail closed) · `service.py` register/issue/revoke/list + audit · `router.py` HTTP. |
| `app/monitoring/` | Read API: health classification (`health.py`, pure), SQL (`queries.py`), serialisation and `load_series` (shared with MCP). |
| `app/mcp/` | `tokens.py` issue/revoke/verify · `service.py` workspace-scoped reads · `server.py` FastMCP tools, rate limit, audit · `router.py` token management API. |
| `app/clerk_auth.py`, `user_auth.py` | RS256 JWT verification (JWKS), allowlist authorisation. |
| `ml/`, `simulator_v2/` | Offline dataset generation, labels, features, baseline models. Not on the request path. |

## Health semantics (current, `app/monitoring/health.py`)

`offline` (no envelope for > 300 s; replay hosts exempt) → `critical`/`warning` (threshold on freshest per-series sample) → `healthy` (≥1 fresh evaluated metric, none over threshold) → `unknown` (no fresh cpu/memory/disk sample). Missing is never zero. Thresholds are module constants returned in every list response; **not yet configurable** (M1 item 5).

## Data provenance

`host_id` prefix + credential kind decide `source` = live | synthetic | replay (`sim-*`, `replay-*`); a live credential cannot be issued for a reserved prefix. Observation time (`metric.timestamp`) and ingestion time (`received_at`) are separate columns/fields.

## Access model (database)

Three layers per table (0002/0003/0004): no grants to `PUBLIC`/Supabase API roles; RLS enabled with policies naming only `uptimetrace_app`; the app role gets the minimum verbs, with column-level UPDATE for the mutable tables. Owner role is used for migrations only.

## MCP interface (opt-in, read-only)

```
 AI agent ──Bearer utm_… (Streamable HTTP, stateless JSON)──▶ /mcp/  (official SDK middleware → SdkTokenVerifier → DB)
                                                              tools: list_hosts · get_host_health · get_host_metrics
                                                              └─ app/mcp/service.py ─▶ app/monitoring/{queries,service,health}  (same code as the console)
```
Workspace comes from the verified token record; visibility is the host registry. See `MCP_INTEGRATION.md` and `SECURITY.md`.

## Planned (not built)

Alert engine + incidents (M2) · experiment runner + reports (M3) · demo service, OTLP collector, Jaeger (M4) · workspace membership/RLS (M6) · Docker Compose deployment · MCP tools for alerts/incidents/experiments/traces.
