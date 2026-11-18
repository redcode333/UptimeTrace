# UptimeTrace backend (Milestone 2: ingestion)

FastAPI + SQLAlchemy 2 + Alembic + Psycopg 3 on Supabase PostgreSQL. The API contract is
`../contracts/telemetry/` and nothing here redefines it.

## Setup
```
pip install -e ".[dev]"
cp .env.example .env        # fill in the owner URL (percent-encode the password!)
python -m alembic upgrade head          # runs as the owner (MIGRATION_DATABASE_URL, else DATABASE_URL)
python -m app.provision_app_role        # one-off: enables the least-privilege role, rewrites .env
python -m app.check_db                  # prints role + privileges only, never credentials
python -m app.check_db --migration
python -m uvicorn app.asgi:app --port 8000
```

## Access model (migration 0002)
Enabling RLS alone is **not** enough on Supabase: new `public` tables are granted to `anon`,
`authenticated` and `service_role` by default and are served by the Data API, and `service_role`
and the owner bypass RLS. The model therefore has three layers:

1. **No grants** for `PUBLIC`, `anon`, `authenticated`, `service_role` on `telemetry_envelopes`, its
   identity sequence and `alembic_version`; default privileges for future `public` tables are revoked too.
2. **RLS enabled**, with policies that name only `uptimetrace_app` (INSERT, SELECT). Anyone re-granted
   access later still sees zero rows.
3. **`uptimetrace_app`**: NOLOGIN until provisioned, no superuser / createrole / createdb / replication /
   bypassrls, not the owner. Privileges: `USAGE` on schema `public`, `SELECT, INSERT` on the table.
   That is exactly what `INSERT .. ON CONFLICT DO NOTHING RETURNING`, the idempotency `SELECT` and
   `/health` (`SELECT 1`) need. No UPDATE, DELETE, TRUNCATE or DDL.

The `postgres` owner is used only for migrations. `service_role` has no access either; if you ever need it,
grant it deliberately in a new migration.

## Simulator data
Simulator rows are identifiable by `host_id`: the CLI refuses anything not starting with `sim-`
(the first runs used `sim-<scenario>` and `sim2-<scenario>`, still matched by the pattern below).
```sql
SELECT host_id, count(*) FROM telemetry_envelopes WHERE host_id ~ '^sim[0-9]*-' GROUP BY 1;  -- inspect
-- DELETE FROM telemetry_envelopes WHERE host_id ~ '^sim[0-9]*-';  -- only when you decide to clean up
```

## Tests (need a throwaway local PostgreSQL; they refuse to run against anything else)
```
docker run -d --name uptimetrace-test-pg -e POSTGRES_PASSWORD=<test-password> \
  -e POSTGRES_DB=uptimetrace_test -p 55432:5432 postgres:16-alpine
# put the matching TEST_DATABASE_URL in backend/.env, then:
python -m pytest
python -m unittest discover -s ../contracts/telemetry/tests     # from backend/: contract suite
```

## Simulator (real HTTP)
```
python -m simulator --scenario cpu_saturation --count 30 --interval 0.5 --seed 7 --host-id web-01
```
Scenarios: `normal_baseline`, `cpu_saturation`, `memory_pressure`, `disk_pressure`, `network_spike`,
`gradual_degradation`. Metric values are reproducible from `--seed`; envelope ids are unique per run.

## Request pipeline (contract order)
size (streamed 1 MiB cap) -> content type -> strict JSON parse -> `schema_version` -> JSON Schema
(`jsonschema`, Draft 2020-12, no `format` assertion) -> semantic checks -> idempotent durable insert -> 202.

## Idempotency
* **Identity:** `envelope_id` (UUID column with a `UNIQUE` constraint — the database is the arbiter).
* **Equivalence:** `content_hash` = SHA-256 of a canonical JSON form of the parsed envelope: keys sorted,
  compact separators, and integral floats collapsed to ints (`1`, `1.0`, `1e0`, `-0.0`/`0` are equal).
  Array order, strings and timestamp spellings are significant (`...:00Z` vs `...:00.0Z` differ).
* **Write path:** one transaction runs `INSERT ... ON CONFLICT (envelope_id) DO NOTHING RETURNING id`.
  Row returned -> new, commit, `202`. No row -> compare the stored hash: equal -> same `202` body,
  nothing stored; different -> `409 envelope_id_conflict`.
* **Concurrency:** under READ COMMITTED a second inserter of the same id waits for the first to commit
  or roll back, so a duplicate is never answered before the original is durable, and exactly one row exists.
* **Acknowledgement:** `202` is built only after the `with session.begin()` block exits, i.e. after COMMIT.
  Any database error -> rollback -> `503 unavailable`.

## Notes
* Open contract decisions kept open: how long envelope ids are remembered; reconciling the same
  observation identity across different envelopes (we store each envelope whole and do not reconcile).
* **PostgreSQL storage-compatibility restriction:** U+0000 inside any string value or object key is rejected
  with `422 validation_error` *before* any database access, because PostgreSQL `text`/`jsonb` cannot store it.
  It is not a contract rule and the JSON Schema is unchanged; without it such an envelope would pass
  validation and fail at insert forever, and the agent would retry it endlessly.
* **Error envelope everywhere:** 404 `not_found`, 405 `method_not_allowed` (with `Allow`), 500 `internal_error`
  and 503 `unavailable` all use `{"error": {"code", "message"}}` with fixed messages. Database failures
  (connection, timeout, pool exhaustion, failed commit) are 503; other unexpected errors are 500.
