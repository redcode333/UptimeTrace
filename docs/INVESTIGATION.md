# Alert investigation

An engineer opens an alert and sees why it fired, the telemetry around it, the lifecycle, related alerts on the same host, and
whether the host recovered. Everything shown is stored evidence. Nothing is inferred, and no language model is involved.

## Trigger and recovery evidence (migration 0007)

Each alert records:

| Field | Meaning | Written |
|---|---|---|
| `trigger_value`, `trigger_threshold`, `comparison` | The breaching value, the limit it was compared with, and the operator (`>=`) | Once, when the alert opens |
| `trigger_observed_at` | When the breaching sample was observed (host last telemetry for `host_offline`) | Once, when the alert opens |
| `recovery_value`, `recovery_observed_at` | The judged value that cleared the condition and when it was observed; for `host_offline`, when the host reported again | When the system recovers the alert |

Existing fields keep their contract. `value` still holds the value at recovery, as before.

* The application role has no UPDATE grant on the trigger columns, so the database refuses to rewrite them.
* Alerts that opened before migration 0007 are backfilled only from values their own events already recorded: the trigger value and
  threshold from the `opened` event, and the recovery value from the `recovered` event. Observation times were never recorded for
  them, so `trigger_observed_at` and `recovery_observed_at` stay NULL. The API reports these as `unavailable`; nothing is estimated.
* A manual close records no recovery evidence. The investigation says the telemetry does not show that the condition cleared.

## Endpoints

All require a browser session and the same workspace gate as the alert routes. An alert in another workspace returns 404
(`alert_not_found`), indistinguishable from a missing one.

* `GET /api/v1/alerts/{id}/investigation?window_minutes=30&related_limit=20&related_offset=0`
  * `window_minutes`: 5 to 120 (default 30), either side of the trigger. Returns 422 outside that range.
  * Telemetry: two bounded windows, around the trigger and around the recovery sample, for the alert's own metric and label set.
    Gaps are null, never zero. The existing series path enforces its point and range limits.
  * Related alerts: same host, opened within the window, paged (`related_limit` up to 100).
  * Host offline alerts have no metric series; `series_note` says so and the host's last telemetry time is the evidence.
* `GET /api/v1/alerts/{id}/investigation/report`: the stored record, or 404 `report_not_found`.
* `POST /api/v1/alerts/{id}/investigation/report`: creates the record once (201). Later calls return the stored record unchanged (200).

## The investigation record

`investigation_reports` holds one immutable record per alert. The application role may INSERT and SELECT only.

The record stores:
* `evidence`: the snapshot the conclusions were derived from (alert, trigger, recovery, host state, telemetry windows, events,
  related alerts, and the monitoring settings version and limits in force).
* `facts`: observed values and recorded events, each with a source.
* `conclusions`: fixed rules applied to facts, each listing the facts it rests on. Examples: "the trigger condition was met"
  (value at or above the limit), "recovery is evidenced by telemetry" (recovered value below the warning limit), and
  "temporal correlation only" for related alerts.
* `not_established`: what the evidence cannot show. This always includes "Root cause is not established", because UptimeTrace
  collects host metrics and liveness only, not logs, processes, deployments or network data.
* `evidence_digest`: SHA-256 of the canonical body.

`verify` recomputes the digest and re-derives facts, conclusions and not-established items from the stored evidence. The API
returns `verified`, `digest_matches` and `rederivation_matches` on every read. A tampered row fails verification.

## Console

Each row on the Alerts page has an Investigate link to `/alerts/{id}`. The page shows the summary, the trigger evidence (with
"Not recorded" where that is the truth), recovery, telemetry charts for the windows, the lifecycle, related alerts with the
correlation caveat, and the record (create, then read on later visits).

## Not in this slice

* No root-cause analysis and no language-model step. The conclusions are fixed rules over recorded evidence.
* No notification, routing or incident grouping.
* The record is created on demand. It is not regenerated when the alert changes afterwards; a later visit shows the stored one.
