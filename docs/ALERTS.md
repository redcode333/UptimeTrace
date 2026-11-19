# Persistent alerts

Implemented in `backend/app/alerts/` (`rules.py` pure rules, `service.py` persistence and sweep, `router.py` browser API),
migration `0006_alerts.py`, console page `frontend/src/features/alerts/AlertsPage.tsx`.

Alerts are raised by the same deterministic health rules that the host page shows (`backend/app/monitoring/health.py`).
No language model is involved, and no alert is created from a guess.

## What raises an alert

| Condition | Opens when | Key | Severity |
|---|---|---|---|
| Threshold | a judged sample of CPU, memory or disk is at or above a configured `warning` or `critical` limit | `metric\|labels`, the same key the host page uses | `warning` or `critical`, as judged |
| Host offline | the host has sent no telemetry for longer than the offline window | `host\|offline` | `critical` |

Limits and windows come from the workspace monitoring settings (`GET/PUT /api/v1/settings/monitoring`). A change applies on the next evaluation.

A host that is **stale** (silent past the stale window but not yet offline) raises nothing new, and existing alerts on it are left as they are, because its data is too old to judge.
A host that is **unknown** (fresh, but with no judged metric) also raises nothing and clears nothing.
Replay hosts are never alerted on.

## Lifecycle

```
(no alert) --breach/offline--> OPEN --acknowledge--> ACKNOWLEDGED
                                  |                      |
                                  +--recovers-----------+--> RESOLVED (recovered, by system)
                                  +--operator resolves-----> RESOLVED (manual, by user)
```

* **One alert per condition.** A partial unique index (`uq_alerts_live_condition`) allows at most one open or acknowledged alert per (workspace, host, condition). A repeated breach updates that alert in place. The database enforces this, so it holds even if two evaluators run at once.
* **Escalation** (warning to critical) keeps the same alert and records a `severity_changed` event.
* **Recovery** resolves the alert only when its condition is **evidenced** as cleared: the metric was judged fresh and is below its limit, or (for host offline) the host is reporting again. A missing, stale or unsampled metric never clears an alert. Missing telemetry is never treated as healthy.
* **Acknowledgement** records who acknowledged and when. It does not stop the alert from recovering or from escalating. Acknowledging twice changes nothing.
* **Manual resolution** closes an alert by hand. If the breach is still present, the next evaluation opens a **new** alert, and the timeline of the old one shows the manual resolution.
* Every change, and its actor and time, is an event on the alert timeline (`opened`, `severity_changed`, `acknowledged`, `recovered`, `resolved_manually`).

## When alerts are evaluated

1. **After each accepted envelope**, in the same request, after the commit. An evaluation failure is logged (exception class only) and never fails the ingestion. The envelope is already durable.
2. **Periodic sweep** every `ALERT_SWEEP_SECONDS` (default 30, `0` disables). This is what notices a host that has stopped reporting, since no ingestion happens then.

Evaluations of one host are serialised with a transaction-scoped advisory lock. One host's failure in the sweep does not stop the other hosts.

## Browser API

All routes need a verified console session and an allowlisted user. Agent credentials are refused (401).

| Route | Purpose |
|---|---|
| `GET /api/v1/alerts?status=active\|resolved\|all&host_id=&limit=` | list (default: active = open or acknowledged) |
| `GET /api/v1/alerts/{id}` | one alert with its timeline |
| `POST /api/v1/alerts/{id}/acknowledge` | acknowledge (409 if already resolved) |
| `POST /api/v1/alerts/{id}/resolve` | resolve by hand (idempotent) |

## Storage and access

* `alerts`, `alert_events`: row-level security on; no grants to the public Data-API roles.
* The application role may insert and read alerts, update only the state columns (never `host_id`, `condition_key` or `labels`), and may never delete. The timeline is append-only for the application role.
* Every table carries `workspace_id`. Queries are scoped to the default workspace, as in the rest of the API. Multi-workspace isolation is milestone M6, not this change.

## Known limitations

* The sweep reads every host's newest samples on each run. This is fine for a single workspace of a few hundred hosts and is not measured beyond that.
* Alert delivery (email, chat, pager) is not implemented. Alerts are visible in the console and through the API only.
* Sustained-duration rules ("above 90 % for 5 minutes") are not implemented. Evaluation uses the newest fresh sample, as the host page does.
* Auto-recovery requires the metric to be reported fresh again. A host that reports only some metrics keeps alerts on the others open.
* Notifications, escalation policies and incident grouping are not implemented.
