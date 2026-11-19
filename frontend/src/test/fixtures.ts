import type { Host, HostDetail, HostList, LatestMetrics, MetricSample, SeriesResponse } from "../api/types";

/** Builders for API-shaped test data. Every value a test cares about is passed explicitly; defaults are neutral. */
export const host = (over: Partial<Host> & { host_id: string }): Host => ({
  hostname: over.host_id, agent_id: "agent-1", service: null, source: "live", connected_infrastructure: true,
  first_seen: "2026-11-01T00:00:00Z", last_seen: "2026-11-13T10:00:00Z", last_seen_age_seconds: 20, reporting_status: "reporting",
  health: { status: "healthy", category: "healthy", reasons: [] },
  latest: { cpu_percent: 10, memory_percent: 20, disk_percent_max: 30, disk_mount_max: "C:", network_rx_bytes_per_second: null, network_tx_bytes_per_second: null },
  ...over,
});

export const hostList = (items: Host[], over: Partial<HostList> = {}): HostList => ({
  items, page: 1, page_size: 25, total: items.length,
  facets: { source: { live: items.length, synthetic: 0, replay: 0 }, health: { healthy: items.length, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0 } },
  thresholds: { host_stale_after_seconds: 90, offline_after_seconds: 300, stale_after_seconds: 300, rules: {} },
  ...over,
});

export const sample = (over: Partial<MetricSample> & { name: string }): MetricSample => ({
  kind: "gauge", unit: "percent", labels: {}, value: 1, observed_at: "2026-11-13T10:00:00Z", stale: false, rate_per_second: null, ...over,
});

export const detail = (h: Host): HostDetail => ({ ...h, metrics: [] });
export const latestOf = (h: Host, metrics: MetricSample[]): LatestMetrics => ({ host_id: h.host_id, source: h.source, last_seen: h.last_seen, metrics });

const T0 = Date.parse("2026-11-13T04:00:00Z");
export const seriesOf = (hostId: string, metric: string, values: (number | null)[], over: Partial<SeriesResponse> = {}): SeriesResponse => ({
  host_id: hostId, source: "live", metric, kind: "gauge", start: "2026-11-13T04:00:00Z", end: "2026-11-13T10:00:00Z", step_seconds: 300, agg: "avg",
  series: [{
    labels: {}, unit: "percent", value_type: "gauge",
    points: values.map((v, i) => ({ t: new Date(T0 + i * 300_000).toISOString(), value: v, samples: v == null ? 0 : 1 })),
  }],
  ...over,
});
