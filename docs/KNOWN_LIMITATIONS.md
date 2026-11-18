# Known limitations

Each item is verified against the code unless marked *(from audit, unverified)*.

## Security / tenancy
* **Single workspace.** One seeded `default` workspace and a user-id allowlist. No membership, roles or per-workspace RLS. Do not onboard independent customers. (M6)
* `hosts.host_id` is globally unique and `telemetry_envelopes` has no `workspace_id`; the frozen v1 envelope cannot carry one. Ownership is derived from the registry join.
* The app role's RLS policies are `USING (true)` — defence in depth against the Data API roles, not tenant separation.
* Every allowlisted user can register hosts and issue/revoke credentials (no admin/viewer split yet).
* Legacy `credentials.json` fallback still works and is not revocable without restart.
* No rate limiting on the browser API or on failed agent authentication.

## MCP
* Off by default; **not for shared or customer deployments** until M6. Static bearer tokens only (no OAuth/discovery). Only the official SDK client has been run against it; no real third-party client and no TLS deployment was tested.
* Three tools only; alerts, incidents, services/traces and experiments are absent because their backends are.
* Rate limit is per process. No UI for token management (API only). Deep links exist for hosts only (no other console pages exist).
* Tool-call audit rows grow without bound (no retention policy).

## Monitoring
* Thresholds and offline interval are constants (85/95 CPU & memory, 90/95 disk; 300 s). No per-host/workspace configuration; no distinct `stale` state (only `offline`).
* Hosts that have never reported appear only in `/api/v1/registry/hosts`, not in `/api/v1/hosts` (D-007); the frontend does not show them yet.
* Series queries find late samples only up to 24 h after `end` (documented in `queries.py`).
* Agent buffer is in-memory: telemetry is lost if the agent process or machine dies before delivery. Linux collection exists but was never run on a real Linux host.

## Missing product capabilities
No persistent alerts, incidents, Failure Readiness, OpenTelemetry/Jaeger, Docker Compose deployment, backup/restore procedure, retention or deletion policy.

## ML / datasets
* **Missing-as-zero** in the deterministic rule: `ml/predict.py:71-73` and `ml/train_baselines.py:169`. Verified. A host with no utilisation data scores risk 0.
* `artifacts/datasets/…seed1…` has 108 of 180 observation partitions (incomplete); seed0 has 180 runs.
* Saved model report was produced on a small subset and thresholds were picked by F1 on validation ("val metrics are optimistic" per `train_baselines.py`). No held-out IID/OOD evaluation has been run. *(IID/OOD part from audit; not re-run in this session)*
* `pyproject.toml` does not declare `scikit-learn`, `xgboost` or `pandas`, which the ML code imports.
* All ML evidence is synthetic; none of it supports production predictive claims.

## Test coverage gaps
* No Playwright/browser end-to-end test. The real-agent test spies on storage; a real-agent → real-DB run is a scripted check, not yet a CI test.
* Frontend has no tests for registry screens (not built yet).
