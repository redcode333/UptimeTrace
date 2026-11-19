# Known limitations

Each item is verified against the code unless marked *(from audit, unverified)*.

## Security / tenancy
* **Single workspace.** One seeded `default` workspace and a user-id allowlist. No membership, roles or per-workspace RLS. Do not onboard independent customers. (M6)
* `hosts.host_id` is globally unique and `telemetry_envelopes` has no `workspace_id`; the frozen v1 envelope cannot carry one. Ownership is derived from the registry join.
* The app role's RLS policies are `USING (true)` — defence in depth against the Data API roles, not tenant separation.
* Every allowlisted user can register hosts and issue/revoke credentials (no admin/viewer split yet).
* Legacy `credentials.json` fallback still works and is not revocable without restart.
* No rate limiting on the browser API or on failed agent authentication.

## Alert investigation (`docs/INVESTIGATION.md`)
* Trigger observation times and recovery observation times exist only for alerts opened after migration 0007. Older alerts show them as unavailable; they were never estimated.
* "Last observed" is the alert's last evaluation time, not the time of the last breaching sample: sample times are not stored between evaluations.
* Recovery is judged against the warning limit in the current settings version. The limit in force at the moment of recovery is not stored separately; the record says so.
* Telemetry windows are bucketed by the existing series path, so short runs show few buckets. Tests prove inclusion, not richness.
* Related alerts are listed within a single time window (±N minutes) on the same host. Correlation is reported as correlation only; no causal ranking is attempted.
* A record is created on demand and never regenerated. If the alert changes afterwards, the stored record does not change.
* The investigation page is built and unit-tested, but has not been checked in a browser; the E2E run used the API and the database only.
* Workspace isolation is the same single-workspace gate as the alert routes. It has been tested with a second workspace row, not with a multi-user tenancy model (see Security / tenancy above).

## Command Center (frontend)
* Built and unit-tested; **not browser-verified**. Authenticated pages need a Clerk session, which is not available in this environment.
* Source labels (LIVE, SYNTHETIC, REPLAY) are derived from host-ID prefixes, not stored. The Command Center says so. Provenance migration is the next step.
* Alert counts by source need the all-sources host list. Above 200 hosts, or when the alert list is capped, scope-filtered counts are marked partial and are never shown as exact.
* Activity covers alert transitions only: no severity changes, agent starts or stops, or host state changes.
* Attention ranking covers only the 100 most recent non-healthy hosts; severity ordering applies to the loaded rows.
* Hosts and Host detail still use the shared utilisation cell's fixed 85/95 defaults; only the Command Center uses configured limits.
* The default parallel `npx vitest run` times out intermittently on this machine (about 0.7 to 0.8 GB free RAM while jsdom workers run). The sequential run `npx vitest run --no-file-parallelism` passes all 15 files and 141 tests. Timeouts were not changed and no assertion was weakened.

## Host Detail (frontend)
* Not browser-verified.
* The host alert list is capped at 200 rows (the list endpoint has no offset). The page says when a host has more.
* The Hosts list and the Command Center's table cells still use fixed defaults in the shared utilisation cell. Only Host Detail and the Command Center use configured limits.
* "Never reported" is inferred from the registry state; a host can only be shown as never reported when it appears in the registry with no telemetry.

## MCP
* Off by default; **not for shared or customer deployments** until M6. Static bearer tokens only (no OAuth/discovery). Only the official SDK client has been run against it; no real third-party client and no TLS deployment was tested.
* Three tools only; alerts, incidents, services/traces and experiments are absent because their backends are.
* Rate limit is per process. Token management has a console page (`/settings/integrations`); the page cannot confirm the endpoint is enabled on the deployment.
* Tool-call audit rows grow without bound (no retention policy).

## Monitoring
* Thresholds and freshness windows are configurable per workspace through the settings API and the Alert Policies page (`/settings/alerts`). There are no per-host thresholds, no sustained-duration rules, and no roles: any allowlisted user can change them. Defaults: 85/95 CPU & memory, 90/95 disk; stale 90 s, offline 300 s. Stale and offline are both states now; the E2E run used 10 s / 20 s windows, so production defaults are not exercised end to end.
* Agent-side collection is not buffered on disk: telemetry queued while the agent process is down is lost.
* The E2E run uses a local test key set in place of Clerk (`backend/tests/clerk_support.py`). Browser sign-in against a real Clerk tenant is untested.
* Hosts that have never reported appear only in `/api/v1/registry/hosts`, not in `/api/v1/hosts` (D-007); the frontend does not show them yet.
* Series queries find late samples only up to 24 h after `end` (documented in `queries.py`).
* Agent buffer is in-memory: telemetry is lost if the agent process or machine dies before delivery. Linux collection exists but was never run on a real Linux host.

## Missing product capabilities
Alerts exist only as threshold and host_offline alerts (no notifications, no routing, no incident grouping). No incidents, Failure Readiness, OpenTelemetry/Jaeger, Docker Compose deployment, backup/restore procedure, retention or deletion policy.

## ML / datasets
* **Missing-as-zero** in the deterministic rule: `ml/predict.py:71-73` and `ml/train_baselines.py:169`. Verified. A host with no utilisation data scores risk 0.
* `artifacts/datasets/…seed1…` has 108 of 180 observation partitions (incomplete); seed0 has 180 runs.
* Saved model report was produced on a small subset and thresholds were picked by F1 on validation ("val metrics are optimistic" per `train_baselines.py`). No held-out IID/OOD evaluation has been run. *(IID/OOD part from audit; not re-run in this session)*
* `pyproject.toml` does not declare `scikit-learn`, `xgboost` or `pandas`, which the ML code imports.
* All ML evidence is synthetic; none of it supports production predictive claims.

## Test coverage gaps
* No Playwright/browser end-to-end test. The real-agent → real-DB workflow (`scripts/e2e/run_workflow.py`) passes but is a manual script, not a CI test, and does not click through the console.
* The full backend suite (including ML files) was not re-run after the alert work; only the affected files were (144 passed).
* Frontend registry, alert, investigation and agent screens have no browser-level verification.
* The investigation E2E (`scripts/e2e/investigation_workflow.py`) is a manual script like the 34-check run; it does not use the Go agent (telemetry is signed by the same credential path) and does not click through the console.
