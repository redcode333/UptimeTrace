# Decisions

Format: ID — decision — why — consequences. Newest last.

**D-001 Preserve the existing stack.** FastAPI + SQLAlchemy 2 + Alembic + psycopg3, PostgreSQL JSONB envelopes, React/Vite/Clerk, Go agent, telemetry contract v1. Why: code is well tested (backend suite, 56 frontend tests, Go tests) and its security posture (three-layer DB access model, fail-closed auth) is deliberate. Nothing in the audit justified replacement.

**D-002 Test safety.** Destructive tests run only against a loopback PostgreSQL that differs from `DATABASE_URL`; the guard in `tests/conftest.py` is kept as-is and new destructive fixtures (registry truncation) depend on it. The M0 container is bound to `127.0.0.1` only.

**D-003 No Kubernetes.** Docker Compose first (per mandate). No multi-node requirement exists.

**D-004 Workspace-aware records from M1.** `workspaces` table, a single seeded `default` workspace, `workspace_id NOT NULL` on every new table. Why: avoids a later schema redesign. Consequence: this is *not* tenant isolation — one allowlist, one workspace, RLS policies are `USING (true)` for the app role. KNOWN_LIMITATIONS lists this; M6 replaces it. The caller can never supply a workspace id (request models use `extra="forbid"`; tested).

**D-005 Credentials move to the database.** Registry-issued tokens (SHA-256 of 256-bit random secret; plaintext returned once with `Cache-Control: no-store`). Why: the JSON file required an API restart to revoke — unacceptable for "revoke safely". No caching, so revocation applies on the next request; DB failure ⇒ 503 (fail closed, agents retry). Legacy `credentials.json` remains as a fallback so existing deployments are not broken; remove after migration.

**D-006 Application role cannot mutate identity.** Column-level `UPDATE` grants only (`agent_credentials.revoked_at/revoked_by`, `hosts.display_name/deactivated_at`); `audit_events` is INSERT/SELECT only. A compromised API process cannot rewrite token hashes or the audit trail (tested).

**D-007 Registry read model is additive.** `GET /api/v1/hosts` (frozen response shape used by the frontend) is unchanged. Registered-but-silent hosts surface via `/api/v1/registry/hosts` with an explicit `telemetry_state` (`awaiting_first_telemetry | reporting | silent | replay`) instead of making `HostOut.last_seen` nullable and rippling through every page.

**D-008 Agent shutdown duplicate is by design.** At-least-once delivery means an in-flight POST interrupted by shutdown can be re-sent by `Drain` with identical bytes; the real API answers idempotently. `test_agent_contract` asserted uniqueness against a non-deduplicating spy and was racy; it now asserts same-id ⇒ identical content.

**D-009 ML fixes deferred to M5, not patched ad hoc.** The missing-as-zero rule (`ml/predict.py:71-73`, `ml/train_baselines.py:169`) is confirmed but changing it invalidates saved reports; it will be fixed together with the re-run of the evaluation.

**D-010 MCP: official SDK, Streamable HTTP, stateless, in-process, opt-in.** No new service, no hand-rolled JSON-RPC. Pinned `<2` until the 2.x API is evaluated. Disabled unless `MCP_ENABLED`; startup refuses a half-configured enablement.

**D-011 MCP auth = static workspace-bound tokens, not OAuth (yet).** An OAuth 2.1 authorization server (registration, PKCE, consent UI) is a large surface that would be built before workspaces and roles exist. Tokens are hashed, scoped, expiring (≤90 d), revocable immediately and verified against the DB per request. Cost: clients that require OAuth discovery cannot connect. Revisit with M6.

**D-012 MCP workspace isolation via the host registry.** Telemetry has no workspace column and the v1 contract is frozen, so a host is visible only if registered in the token's workspace; unregistered telemetry is invisible. Another workspace's host and a missing host return the same error.

**D-013 No tools for absent backends, and no resources or prompts.** Three tools shipped; reasoning in MCP_INTEGRATION.md.

**D-014 Series logic extracted to `monitoring.service.load_series`.** One implementation for console and MCP. Side effect: for an unknown host the console series route now answers 404 before range validation (previously 422 first); no existing test depended on the old order (full regression result in IMPLEMENTATION_STATUS.md).
