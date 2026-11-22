# Deployment

Current public state, verified by HTTP checks on the dates noted in the README. Variable **names** are listed; values are never recorded here.

## Components

| Component | Where | Status |
|---|---|---|
| Frontend (static Vite build) | Vercel project `uptimetrace-web` · `https://uptimetrace-web.vercel.app` | Deployed. Landing and sign-in pages load with no console errors. |
| API (FastAPI, Python 3.12) | Vercel project `uptimetrace-api` · `https://uptimetrace-api.vercel.app` | Deployed. `/health` returns `status: ok`, `database: ok`. |
| Database | Supabase hosted PostgreSQL (pooler) | Reached as `uptimetrace_app` over TLS. Migration revision 0009. |
| Authentication | Clerk **development** instance | Demonstration only. Production instance not configured. |
| MCP endpoint | `https://uptimetrace-api.vercel.app/mcp/` | Deployed, opt-in (`MCP_ENABLED=true`). Requires a bearer token. |
| Telemetry agent | Windows host, run locally | Sends to the hosted API with a registry-issued credential. |

## Frontend build variables (names)

- `VITE_CLERK_PUBLISHABLE_KEY` (public key, required)
- `VITE_API_BASE_URL`: the API origin. Empty means same-origin, which is wrong for this split deployment.
- `VITE_MCP_PUBLIC_URL`: the MCP base URL shown on the Integrations page. Unset shows "MCP not publicly deployed".

## API environment variables (names)

- `DATABASE_URL`: the application connection (`uptimetrace_app`). Set `sslmode=require`.
- `CLERK_ISSUER`, `CLERK_AUTHORIZED_PARTIES` (also the CORS origins), `UPTIMETRACE_ALLOWED_USER_IDS`
- `MCP_ENABLED`, `MCP_PUBLIC_URL`, `MCP_ALLOWED_HOSTS`, `CONSOLE_BASE_URL`

`MIGRATION_DATABASE_URL` (the owner role) must **not** be deployed to the API. Migrations run from a local environment only.

## Serverless constraints

- **No background worker.** The periodic alert sweep (`alert_sweep_seconds`) runs in a long-lived process. On Vercel it does not run. Alerts are evaluated on each telemetry ingest.
- **Cold starts.** The first request after an idle period loads the application, including its database engine.
- **Rate limits are per process.** The MCP limit (60 calls per minute per token) is kept in memory per instance, so several warm instances give a higher effective limit.
- **Size.** The API is deployed from a slim requirements file (39 pinned packages, no ML training packages). Windows-only dependencies such as `pywin32` must be excluded, or the Linux build fails.
- **Packaging gap.** The staged deployment folder (entry point `api/index.py`, the `backend/` package, `contracts/`, `requirements.txt`, `vercel.json`) is kept outside the repository. Recreating it requires the same layout. A repository-tracked deploy configuration is not yet in place.

## ML on the deployment

- The experimental outage adapter is **not** exposed by the API. It runs locally and on request in the repository code.
- The ECOD anomaly artifact (62.7 MB, git-ignored) is **not** deployed.
- No ML output appears in the public dashboard.

## Verification checks

- `GET https://uptimetrace-api.vercel.app/health` → 200, `{"status":"ok","app":"ok","database":"ok"}`.
- `GET https://uptimetrace-web.vercel.app/` → 200.
- Unauthenticated requests to `/mcp/`, `/api/v1/hosts`, `/api/v1/registry/mcp-tokens` and `/api/v1/telemetry` → 401.
- Frontend bundle references the API origin and contains no localhost address.

## Known deployment limitations

- Clerk development instance (see above).
- TLS verifies encryption (`require`) but not the server certificate. `verify-full` needs Supabase's root certificate, which is not yet installed.
- The `uptimetrace_app` role has UPDATE and DELETE on several tables, beyond the minimum. This was accepted for the submission and should be reviewed after it.
- No health alerting for the API itself beyond the `/health` endpoint.
