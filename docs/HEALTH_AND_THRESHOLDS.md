# Host health, freshness and thresholds

Implemented in `backend/app/monitoring/{health,settings}.py`; computed **at query time** from the newest stored telemetry (no background polling, no state to drift). Console, API and MCP all call the same functions.

## Two separate questions

1. **Freshness**: is the data current enough to judge? (`age = now − last_seen`, where `last_seen` is the newest envelope's timestamp.)
2. **Thresholds**: is there a concern in the current data? (per-metric warning/critical limits.)

Freshness is evaluated first; a late or absent host is never judged on its last values.

## States

| Status | Category | Rule (age in seconds; defaults stale 90, offline 300; both configurable) |
|---|---|---|
| `healthy` | healthy | age ≤ stale_after, ≥1 evaluable cpu/memory/disk sample, none at or above its warning limit |
| `warning` | degraded | age ≤ stale_after and a metric ≥ warning (and none ≥ critical) |
| `critical` | degraded | age ≤ stale_after and a metric ≥ critical |
| `stale` | stale | stale_after < age ≤ offline_after. Data is late; last values are **not** evaluated |
| `offline` | offline | age > offline_after |
| `unknown` | unknown | age ≤ stale_after but no evaluable metric (missing, or every sample older than 300 s relative to the host's last envelope) |

* **DEGRADED** is the category for `warning` and `critical` (the API keeps the two severities; `health.category` groups them).
* Boundaries are inclusive on the fresher side: age exactly `stale_after` is still evaluated; `stale_after + ε` is stale (tested to the millisecond).
* A metric exactly at its limit counts as breached (`>=`).
* Missing metrics are skipped, never read as 0. A host whose only samples are non-health metrics is `unknown`.
* **Replay** hosts replay stored history, so silence is their normal end: never `stale`/`offline` by the clock.
* A host that is registered but has never reported has no telemetry: the registry shows `awaiting_first_telemetry`, MCP reports health `unknown`, and it is absent from `/api/v1/hosts`. An unregistered host that never reported does not exist for health at all.
* `reporting_status` follows the same freshness rules: `reporting` (≤ stale_after) · `stale` · `silent` (> offline_after) · `replay`.
* Clock note: freshness uses the agent-stamped envelope time. The ingest contract bounds future skew to 5 s; a host whose clock runs slow looks older than it is. Receipt-time freshness is a possible later hardening.

## Configuration

`GET/PUT /api/v1/settings/monitoring` (console session required). One document per workspace; compare-and-set on `version` (409 on a concurrent edit); every change is audited (`monitoring_settings.updated`). No row means built-in defaults.

```json
{ "freshness": { "stale_after_seconds": 90, "offline_after_seconds": 300 },
  "rules": { "system.cpu.utilization":    { "warning": 85, "critical": 95 },
             "system.memory.utilization": { "warning": 85, "critical": 95 },
             "system.disk.utilization":   { "warning": 90, "critical": 95 } } }
```
Validation: stale 10–3600 s; offline > stale and ≤ 86400 s; `0 < warning < critical ≤ 100`; exactly these three metrics; unknown keys rejected; workspace id never accepted from the request.

## Conditions and the M2 alert lifecycle

Every threshold breach is a `Reason` with a stable `key` = `metric|labels` (independent of value and severity), exposed as `health.reasons[].key`. A condition that escalates warning → critical keeps the same key, so M2 can deduplicate on `(workspace, host, key)` and update one alert instead of creating another. Not done in M1: persistence, alert state, acknowledgement.

**Evaluation windows** ("above 90 % for 5 minutes") need history and belong to the M2 alert engine; health uses the newest fresh sample. No setting exists that the code does not honour.
