# UptimeTrace read API (P3)

Backend read endpoints over the persisted telemetry. They read `telemetry_envelopes` as it exists: no new tables, no migration, no new configuration. Code: `backend/app/monitoring/`. Tests: `backend/tests/test_monitoring.py`.

**Authentication: none yet.** These routes are unauthenticated until the Clerk milestone. Do not expose the API publicly before then; the ingestion route keeps its own scoped credentials.

All timestamps are UTC (RFC 3339, trailing `Z`). Errors use the existing envelope `{"error": {"code", "message", "details"?}}` with codes `invalid_request` (422), `host_not_found` (404).

## Endpoints

| Method and path | Purpose |
|---|---|
| `GET /api/v1/hosts` | Paginated host discovery with health and latest summary, plus facets and the health thresholds |
| `GET /api/v1/hosts/{host_id}` | One host: identity, source, first and last seen, agent, health, reporting status, latest metrics |
| `GET /api/v1/hosts/{host_id}/metrics/latest` | Newest sample per metric series (counters carry a derived rate) |
| `GET /api/v1/hosts/{host_id}/metrics/series` | Bucketed time series for one metric of one host |

### `GET /api/v1/hosts`
`search` (hostname or host id substring, case-insensitive), `source` (`live`, `synthetic`, `replay`; repeat or comma-separate), `health` (`healthy`, `warning`, `critical`, `offline`, `unknown`; same forms), `sort` (`last_seen` default, `hostname`), `order` (`asc`, `desc`; default `desc` for last_seen and `asc` for hostname), `page` (from 1), `page_size` (1-200, default 50).
Response: `items`, `page`, `page_size`, `total`, `facets` (each facet ignores its own filter), `thresholds`.

### `GET /api/v1/hosts/{host_id}/metrics/series`
`metric` (required, contract name), `start` and `end` (RFC 3339 with offset; defaults: end = now, start = end minus 6 hours; start inclusive, end exclusive; at most 31 days), `step` (seconds, at least 10; omitted = chosen so there are at most 720 points; at most 1500 points), `agg` (`avg` default, `min`, `max`, `last`).
Buckets are anchored at `start`. The response has one series per label set (for example per disk mount or interface) and a dense grid of points; a bucket with no data has `value: null`.
Gauges are aggregated as-is. **Counters are returned as per-second rates** (`value_type: counter_rate`, unit `<unit>_per_second`).

## Data source
Not stored per row. It is derived from the host id with the same rules ingestion enforces for credentials: `sim<digits>-` is `synthetic`, `replay-` is `replay`, everything else is `live`. `connected_infrastructure` is true only for `live`.

## Health (deterministic, no ML input)
- `offline`: not a replay host and last envelope older than 300 s. Overrides everything.
- `critical` / `warning`: any fresh measurement at or above its threshold: cpu and memory 85 / 95, disk 90 / 95 (percent).
- `healthy`: at least one measurement evaluated and none crossed a threshold.
- `unknown`: no fresh cpu, memory or disk measurement to evaluate. Missing metrics are never treated as zero.
- Replay hosts are never offline by the clock (`reporting_status: replay`).
- A measurement older than 300 s relative to the host's last envelope is `stale` and ignored for health and for the headline numbers.

## Counter rates
A rate comes only from two consecutive samples of one series. No rate (null / gap) when the counter decreased (reset), the interval is zero or negative, or the interval exceeds 900 s. A reset is therefore a gap, never a spike.

## Query strategy
See the header of `backend/app/monitoring/queries.py`. Measured on 295,205 generated envelopes (5 hosts, 41 days, throwaway test database): host list 2-3 ms (a plain `GROUP BY` takes 42 ms), latest metrics for 5 hosts 24 ms, 6-hour series 5-8 ms, 7-day series 56-90 ms, 31-day series about 190 ms. The existing index `(host_id, envelope_timestamp)` serves all of them; no new index was added.

## Known limitations
- Unauthenticated (see above). No workspace scoping.
- Operating system is not part of telemetry v1, so it is not returned.
- Host list reads every host (cheap at the measured scale); it is not paginated in SQL, and a hosts summary table would be the next step far beyond thousands of hosts.
- A series query finds observations delivered up to 24 hours after the window ends; later flushes are missed.
- Latest metrics look at each host's newest 1000 envelopes within one hour of its last envelope; a metric absent there is reported as absent.
- Series are one host and one metric per request; multi-host charts make one request per host.
- No ML or risk endpoints; health never reads a prediction.
