# Authentication boundary (P4, checkpoint 1)

UptimeTrace uses Clerk for sign-in. The browser app signs users in; the FastAPI backend verifies the session token on every browser-facing request. **This is an authentication boundary plus a single-workspace allowlist. It is not multi-tenant authorization.**

## What is protected
| Route | Requirement |
|---|---|
| `GET /health` | Public |
| `POST /api/v1/telemetry` | Agent bearer credential (unchanged; a Clerk session cannot ingest) |
| `GET /api/v1/me` | Valid Clerk session. Returns `{user_id, authorized}` so a new user can read their own id |
| `GET /api/v1/hosts…` (all four read routes) | Valid Clerk session **and** the user id is in `UPTIMETRACE_ALLOWED_USER_IDS` |

Status codes: 401 missing or invalid session (one fixed message, `WWW-Authenticate: Bearer`); 403 valid session but not allowlisted; 503 the signing keys cannot be fetched (never a pass). With no verifier configured every protected route is 401.

## Token verification (`backend/app/clerk_auth.py`)
RS256 only, listed explicitly (blocks `alg=none` and HMAC-with-the-public-key forgeries). Signature against `{CLERK_ISSUER}/.well-known/jwks.json` (cached, refreshed on an unknown `kid`). `exp`, `nbf`, `iat` with 5 s leeway. `iss` must equal `CLERK_ISSUER` exactly. `sub` required. `azp` must be one of `CLERK_AUTHORIZED_PARTIES`; a token without `azp` is rejected (stricter than Clerk's minimum). The token travels in `Authorization: Bearer`, never a cookie, so CSRF does not apply. CORS allows only the authorized origins, `GET` only, no credentials. No Clerk secret key is needed: only public keys are used.

## Setup
1. In the Clerk dashboard create an application. Enable Email (with password) and Google. Password reset is part of Clerk's sign-in flow.
2. Copy the **Publishable key** into `frontend/.env.local` as `VITE_CLERK_PUBLISHABLE_KEY` (copy `frontend/.env.example`).
3. Copy the **Frontend API URL** (shown on the API keys page) into `backend/.env` as `CLERK_ISSUER`, and set `CLERK_AUTHORIZED_PARTIES=http://localhost:5173`.
4. Start the backend (`uvicorn app.asgi:app`), then the frontend (`npm run dev` in `frontend/`). The API refuses to start without the three variables in `backend/.env.example`.
5. Sign in once. You will see "This account cannot view the workspace yet" with your user id. Put it in `UPTIMETRACE_ALLOWED_USER_IDS`, restart the backend, reload.
6. Recommended: in the Clerk dashboard restrict sign-ups (allowlist or restricted mode) so strangers cannot create accounts at all.

## What is NOT implemented, and is required before production
- **Workspace model and per-workspace data isolation.** Every allowlisted user sees all telemetry. There is no `workspace_id` on telemetry or users, no membership table, and no authorization check tying a request to a workspace.
- Roles (admin, member, viewer) and an audit trail.
- Agent credential management through the API (credentials are still issued with `python -m app.auth`).
- Rate limiting on the read API, and a deployment-time security review (TLS, CSP headers, secret handling).
- The real Clerk flow has not been exercised against a live Clerk instance in this milestone: the backend is tested with locally generated RS256 keys, the frontend with a mocked SDK. First sign-in with your keys is the final check.
