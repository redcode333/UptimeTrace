# Security

No prior SECURITY document existed; auth notes live in `docs/auth/clerk-auth.md`. This file records the security model as implemented and its known gaps.

## Trust boundaries

| Caller | Credential | Reaches | Check |
|---|---|---|---|
| Agent | `utk_` bearer, bound to agent_id + host_ids + kind, DB-backed (or legacy file) | `POST /api/v1/telemetry` only | hash lookup per request, scope check vs envelope, revocation immediate |
| Console user | Clerk RS256 JWT (JWKS, issuer, azp) + user-id allowlist | `/api/v1/hosts…`, `/api/v1/registry…` | verified every request; 401/403 |
| MCP client | `utm_` bearer, workspace-bound, scoped, expiring | `/mcp/` only | per-request DB verification; scope per tool; rate limit; audit |

Token families are disjoint by prefix and by table; cross-use is tested to fail.

## Controls implemented

* Secrets are hashed at rest (SHA-256 of 256-bit random tokens); shown once with `no-store`; never in logs, audit rows or responses (tested).
* Database: no grants to Supabase API roles, RLS on, app role least-privilege with column-level UPDATE; audit table append-only; token hashes immutable (tested).
* Fail closed: DB error during agent or MCP token verification ⇒ not accepted (503 / 401). Missing Clerk config ⇒ browser API answers 401.
* MCP: no SQL/shell/arbitrary-path tools; closed argument schemas (metric whitelist, id pattern, bounded limits/ranges/points); workspace never from arguments; cross-workspace ids indistinguishable from non-existent.
* Caller-supplied `workspace_id` is rejected on every request model (`extra="forbid"`).
* Destructive test fixtures are guarded to loopback databases (`tests/conftest.py`).

## Known gaps (release blockers for independent customers are marked ⛔)

* ⛔ Single effective tenant in the console and read API; no membership/roles; RLS policies for the app role are `USING (true)`. MCP isolation is enforced in application code via the registry join, verified by tests, but not by database RLS.
* ⛔ Any allowlisted user can issue/revoke agent and MCP tokens (no admin role).
* No OAuth 2.1 for MCP; static bearer tokens only. Token theft = access until expiry/revocation.
* Rate limiting: MCP per-token in-process only; none on agent auth failures or the browser API.
* Telemetry itself is not workspace-tagged (frozen v1 contract); ownership is inferred from the registry.
* No retention/deletion policy, no backup/restore procedure, no dependency or container scan performed.
* Legacy `credentials.json` fallback is not revocable without restart.
