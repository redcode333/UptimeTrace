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

## Current public deployment

- Frontend: Vercel static site (`uptimetrace-web.vercel.app`), built with `VITE_API_BASE_URL` pointing at the API.
- API: Vercel Python function (`uptimetrace-api.vercel.app`), the same ASGI app as `uvicorn app.asgi:app`, through `api/index.py`.
- Database: hosted Supabase PostgreSQL, reached as `uptimetrace_app` over TLS (`sslmode=require`). Migration revision 0009.
- Serverless consequence: there is no persistent background worker. Alerts are evaluated on ingest. The periodic sweep (`alert_sweep_seconds`) does not run.
- See `DEPLOYMENT.md` for the environment variables and verification checks.

## ML inference path (experimental, local and hosted telemetry)

```
 stored telemetry_envelopes (per host, last 6 h)
   └─ app/ml_inference/live.py        metric names → cpu · mem · disk · rx · tx; per-mount and per-interface labels kept
        └─ app/ml_inference/inputs.py  explicit disk-role mapping (root/data mounts) · network: one interface only
             │                         fail closed: history < 1 h, stale series, missing or ambiguous mapping → no score
             └─ ml/features.py          the verified feature pipeline (compute_features), unchanged
                  └─ app/ml_inference/outage.py   saved XGBoost + schema + threshold, unchanged → experimental risk score
```

- The ECOD anomaly artifact (`artifacts/models/anomaly_ecod_v1/`, 62.7 MB) is locally verified and git-ignored. It is not loaded by the API.
- Results are computed on request. They are not yet persisted, and there are no ML endpoints or dashboard panels.

## Planned (not built)

Persistence of inference results (migration 0010 is proposed, not applied; its review package is kept outside the repository) · authenticated ML endpoints · dashboard panels for ML · MCP tools for alerts, incidents, experiments and traces · Docker Compose deployment.

Review note: the planned list above predates `app/alerts`, `app/investigation`, `app/rehearsal` and `app/traces`, which exist in the repository. Their status is not restated here and should be verified before the list is relied on.
