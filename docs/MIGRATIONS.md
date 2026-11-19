# Migrations: review and runbook

Chain: `0001` telemetry_envelopes → `0002` least-privilege model → `0003` workspaces/hosts/agent_credentials/audit_events → `0004` mcp_tokens → `0005` monitoring_settings.
Alembic runs as the **owner** role (`MIGRATION_DATABASE_URL`); the API runs as `uptimetrace_app` (never the owner).

## What was verified (disposable loopback PostgreSQL 16, `tests/test_migration_roundtrip.py`)

* Data written under 0002 survives upgrade to head; the `default` workspace is seeded exactly once.
* `downgrade 0002` removes every new table, keeps `telemetry_envelopes` rows, and restores the 0002 schema **exactly** (columns, indexes, constraints, policies, privileges compared before/after).
* up → down → up produces a schema identical to a fresh upgrade.
* With Supabase-like roles (`anon`, `authenticated`, `service_role`, plus default grants on future tables) none of them can SELECT/INSERT/UPDATE/DELETE/TRUNCATE any application table, and RLS is on for all.
* Grants for the app role are column-level where tables are mutable (`agent_credentials(revoked_at, revoked_by)`, `hosts(display_name, deactivated_at)`, `mcp_tokens(revoked_at, revoked_by)`, `monitoring_settings(config, version, updated_at, updated_by)`); `audit_events` is INSERT/SELECT only. Verified by attempting forbidden statements as the real app role.

## Review notes

* **Downgrade is destructive for new data**: `0003`/`0004`/`0005` downgrades `DROP TABLE` the registry, credentials, audit trail, MCP tokens and settings. Telemetry is untouched. Never downgrade a deployment that has issued credentials without exporting them (hashes only; agents would need new tokens anyway).
* `gen_random_uuid()` is core PostgreSQL 13+ (Supabase runs 15+), so no extension is required. Not verified against the real instance.
* All DDL is transactional in PostgreSQL; a failed upgrade rolls back whole revisions.
* `host_id` is globally unique (`uq_hosts_host_id`): an M6 decision (see DECISIONS D-004).
* 0003 does **not** back-fill the registry from telemetry that already exists in a deployment. Existing agents keep working through the legacy `credentials.json` fallback; hosts are registered and credentials re-issued by an operator. Until then, those hosts are visible to the console but **invisible to MCP** (by design).

## NOT verified

The real Supabase database has not been touched, so these are unverified there: current revision (expected `0002`), PostgreSQL version, that the `postgres` owner may create/alter the objects as in the throwaway cluster, and the default-privilege state of the `public` schema.

## Pre-deployment checklist for the Supabase database (requires explicit approval; not done)

1. Take a backup (`pg_dump` of the database or a Supabase point-in-time restore point) and confirm it restores to a scratch instance.
2. Read-only checks as the owner: `SELECT version_num FROM alembic_version;` (expect `0002`), `SELECT version();`, `\dp public.*`.
3. Run `python -m alembic upgrade head` from `backend/` with `MIGRATION_DATABASE_URL` set. Do not run `provision_app_role` again unless the app role is not yet provisioned.
4. Post-checks: `python -m app.check_db`, then confirm as `uptimetrace_app` that `SELECT` works and `DELETE FROM audit_events` is refused.
5. Register hosts and issue credentials through the console API; re-point agents at the new tokens; then remove `credentials.json`.
6. Rollback: `python -m alembic downgrade 0002` (drops the new tables; keep the backup from step 1).
