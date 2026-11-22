import type { ApiClient } from "../../../api/client";
import type {
  Alert,
  AlertHistogram,
  AlertList,
  AlertStatusFilter,
  Distribution,
  DistributionMetric,
  Host,
  HostDetail,
  HostList,
  HostListParams,
  HealthStatus,
  LatestMetrics,
  MetricSample,
  Ranking,
  RankedMetric,
  SeriesResponse,
  Source,
  Thresholds,
} from "../../../api/types";
import type { Rehearsal, RehearsalScenario, RehearsalTarget } from "../../../api/rehearsalTypes";

/**
 * Demonstration fleet for the public landing page. These hosts and readings are invented to show what the console looks
 * like. They are not customer telemetry, and the showcase says so. The client only answers reads; every write is refused
 * because the showcase is read-only.
 */

type Spec = {
  name: string;
  source: Source;
  health: HealthStatus;
  cpu: number;
  mem: number;
  disk: number;
  env: string;
  offline?: boolean;
  alert?: { metric: string; value: number; threshold: number; level: "warning" | "critical" };
};

const SPECS: Spec[] = [
  { name: "edge-eu-1", source: "live", health: "healthy", cpu: 31, mem: 54, disk: 42, env: "production" },
  { name: "edge-eu-2", source: "live", health: "warning", cpu: 84, mem: 62, disk: 47, env: "production", alert: { metric: "system.cpu.utilization", value: 84, threshold: 80, level: "warning" } },
  { name: "api-eu-1", source: "live", health: "healthy", cpu: 46, mem: 71, disk: 58, env: "production" },
  { name: "api-us-1", source: "live", health: "critical", cpu: 97, mem: 88, disk: 63, env: "production", alert: { metric: "system.cpu.utilization", value: 97, threshold: 95, level: "critical" } },
  { name: "db-primary", source: "live", health: "healthy", cpu: 38, mem: 81, disk: 74, env: "production" },
  { name: "db-replica", source: "synthetic", health: "healthy", cpu: 22, mem: 49, disk: 36, env: "staging" },
  { name: "worker-batch-3", source: "synthetic", health: "offline", cpu: 0, mem: 0, disk: 0, env: "staging", offline: true },
  { name: "cache-01", source: "synthetic", health: "healthy", cpu: 12, mem: 33, disk: 19, env: "staging" },
  { name: "cache-02", source: "synthetic", health: "stale", cpu: 18, mem: 35, disk: 21, env: "staging" },
  { name: "ingest-sim-1", source: "synthetic", health: "warning", cpu: 88, mem: 57, disk: 44, env: "staging", alert: { metric: "system.memory.utilization", value: 89, threshold: 85, level: "warning" } },
  { name: "replay-2024-07", source: "replay", health: "healthy", cpu: 27, mem: 45, disk: 52, env: "archive" },
  { name: "replay-2024-08", source: "replay", health: "healthy", cpu: 33, mem: 47, disk: 55, env: "archive" },
];

const NOW = Date.now();
const iso = (ms: number) => new Date(ms).toISOString();
const SEC = 1000;

const THRESHOLDS: Thresholds = {
  host_stale_after_seconds: 90,
  offline_after_seconds: 300,
  stale_after_seconds: 300,
  rules: {
    "system.cpu.utilization": { warning: 80, critical: 95 },
    "system.memory.utilization": { warning: 85, critical: 95 },
  },
};

function sample(name: string, unit: string, value: number, observed: number, kind: "gauge" | "counter" = "gauge"): MetricSample {
  return { name, kind, unit, labels: {}, value, observed_at: iso(observed), stale: false, rate_per_second: kind === "counter" ? value / 60 : null };
}

const HOSTS: Host[] = SPECS.map((s, i) => {
  const seen = s.offline ? NOW - 40 * 60 * SEC : NOW - (3 + (i % 5) * 4) * SEC;
  const reporting: Host["reporting_status"] = s.source === "replay" ? "replay" : s.offline ? "silent" : s.health === "stale" ? "stale" : "reporting";
  const reasons = s.alert
    ? [{ metric: s.alert.metric, labels: {}, value: s.alert.value, threshold: s.alert.threshold, level: s.alert.level, key: s.alert.metric }]
    : [];
  return {
    host_id: `demo-${String(i + 1).padStart(2, "0")}`,
    hostname: s.name,
    agent_id: `agent-demo-${i + 1}`,
    service: null,
    environment: { id: `env-${s.env}`, name: s.env },
    region: null,
    cluster: null,
    services: [],
    source: s.source,
    connected_infrastructure: false,
    first_seen: iso(NOW - 14 * 24 * 3600 * SEC),
    last_seen: iso(seen),
    last_seen_age_seconds: Math.round((NOW - seen) / SEC),
    reporting_status: reporting,
    health: {
      status: s.health,
      category: s.health === "critical" || s.health === "warning" ? "degraded" : s.health,
      reasons: s.offline ? [] : reasons,
    },
    latest: {
      cpu_percent: s.offline ? null : s.cpu,
      memory_percent: s.offline ? null : s.mem,
      disk_percent_max: s.offline ? null : s.disk,
      disk_mount_max: s.offline ? null : "/",
      network_rx_bytes_per_second: s.offline ? null : 180_000 + i * 9_000,
      network_tx_bytes_per_second: s.offline ? null : 96_000 + i * 6_000,
    },
  };
});

const byId = (id: string) => HOSTS.find((h) => h.host_id === id);

/** Fixed positions in the demonstration fleet. Throws if the table is edited without updating these references. */
const hostAt = (i: number): Host => {
  const h = HOSTS[i];
  if (!h) throw new Error(`Demonstration host ${i} is missing`);
  return h;
};

function metricsFor(h: Host): MetricSample[] {
  const at = Date.parse(h.last_seen);
  if (h.latest.cpu_percent === null) return [];
  return [
    sample("system.cpu.utilization", "%", h.latest.cpu_percent, at),
    sample("system.memory.utilization", "%", h.latest.memory_percent ?? 0, at),
    sample("system.disk.utilization", "%", h.latest.disk_percent_max ?? 0, at),
    sample("system.network.rx", "B/s", h.latest.network_rx_bytes_per_second ?? 0, at, "counter"),
    sample("system.network.tx", "B/s", h.latest.network_tx_bytes_per_second ?? 0, at, "counter"),
  ];
}

/** A gently varying trace with one visible swing, so the charts have shape without looking random. */
function trace(seed: number, base: number, amp: number, n: number) {
  return Array.from({ length: n }, (_, k) => {
    const v = base + amp * Math.sin(k / 4 + seed) + (k > n * 0.55 && k < n * 0.7 ? amp * 1.4 : 0);
    return Math.max(0, Math.min(100, Math.round(v * 10) / 10));
  });
}

const ALERTS: Alert[] = SPECS.flatMap((s, i) => {
  if (!s.alert) return [];
  const host = hostAt(i);
  return [{
    id: `demo-alert-${i + 1}`,
    host_id: host.host_id,
    condition_key: s.alert.metric,
    rule: "threshold" as const,
    metric: s.alert.metric,
    labels: {},
    severity: s.alert.level,
    state: s.alert.level === "critical" ? "open" : "acknowledged",
    value: s.alert.value,
    threshold: s.alert.threshold,
    opened_at: iso(NOW - (6 + i) * 60 * SEC),
    last_evaluated_at: iso(NOW - 8 * SEC),
    acknowledged_at: s.alert.level === "critical" ? null : iso(NOW - 3 * 60 * SEC),
    acknowledged_by: s.alert.level === "critical" ? null : "on-call",
    resolved_at: null,
    resolved_by: null,
    resolution: null,
  }];
});

ALERTS.push({
  id: "demo-alert-resolved",
  host_id: hostAt(4).host_id,
  condition_key: "system.memory.utilization",
  rule: "threshold",
  metric: "system.memory.utilization",
  labels: {},
  severity: "warning",
  state: "resolved",
  value: 88,
  threshold: 85,
  opened_at: iso(NOW - 3 * 3600 * SEC),
  last_evaluated_at: iso(NOW - 2 * 3600 * SEC),
  acknowledged_at: null,
  acknowledged_by: null,
  resolved_at: iso(NOW - 2 * 3600 * SEC),
  resolved_by: "recovered",
  resolution: "recovered",
});

function inScope(sources: Source[] | undefined) {
  return (h: Host) => !sources || sources.includes(h.source);
}

function bucketCounts(start: number, end: number, bucketSeconds: number) {
  const n = Math.max(1, Math.ceil((end - start) / (bucketSeconds * 1000)));
  return Array.from({ length: n }, (_, k) => {
    const t = start + k * bucketSeconds * 1000;
    const hit = ALERTS.filter((a) => {
      const o = Date.parse(a.opened_at);
      return o >= t && o < t + bucketSeconds * 1000;
    });
    // Alerts are recent, so place them in the buckets they opened in and keep the rest at zero.
    const critical = hit.filter((a) => a.severity === "critical").length;
    const warning = hit.length - critical;
    return { start: iso(t), critical, warning };
  });
}

const REHEARSAL_SCENARIO: RehearsalScenario = {
  id: "cpu-saturation",
  title: "CPU saturation",
  summary: "Drives a synthetic host's CPU above the critical limit, holds it, then recovers.",
  metric: "system.cpu.utilization",
  metric_label: "CPU utilisation",
  affects: "One synthetic host",
  interval_seconds: 15,
  planned_envelopes: 12,
  expected_duration_seconds: 180,
  phases: [{ name: "baseline", ticks: 3 }, { name: "fault", ticks: 6 }, { name: "recovery", ticks: 3 }],
  limits: { max_envelopes: 24, consecutive_failure_limit: 3, detection_budget_seconds: 120 },
};

function rehearsal(id: string, over: Partial<Rehearsal>): Rehearsal {
  const created = NOW - 2 * 3600 * SEC;
  return {
    id,
    host_id: hostAt(5).host_id,
    scenario: "cpu-saturation",
    metric: "system.cpu.utilization",
    execution: "COMPLETED",
    execution_reason: null,
    detection: "DETECTED",
    detection_reason: null,
    created_by: "demo",
    created_at: iso(created),
    started_at: iso(created + 5 * SEC),
    ended_at: iso(created + 180 * SEC),
    last_progress_at: iso(created + 180 * SEC),
    stop_requested_at: null,
    stop_requested_by: null,
    phase: "cleanup",
    planned_envelopes: 12,
    sent_envelopes: 12,
    accepted_envelopes: 12,
    failed_envelopes: 0,
    fault_sent: 6,
    fault_accepted: 6,
    recovery_accepted: 3,
    fault_confirmed_at: iso(created + 60 * SEC),
    fault_observed_value: 96,
    fault_ended_at: iso(created + 135 * SEC),
    recovery_sent_at: iso(created + 135 * SEC),
    cleanup_status: "recovered",
    alert_id: "demo-alert-4",
    alert_opened_at: iso(created + 74 * SEC),
    alert_resolved_at: iso(created + 170 * SEC),
    detection_delay_seconds: 14,
    recovery_observed: true,
    recovery_seconds: 35,
    evidence_useful: true,
    evidence_points: 18,
    error_class: null,
    config: { warning_limit: 80, critical_limit: 95 },
    steps: null,
    ...over,
  };
}

const REHEARSALS: Rehearsal[] = [
  rehearsal("demo-rehearsal-1", {}),
  rehearsal("demo-rehearsal-2", {
    created_at: iso(NOW - 26 * 3600 * SEC),
    detection: "UNDETECTED",
    detection_reason: "The alert did not open within the detection budget.",
    alert_id: null,
    alert_opened_at: null,
    alert_resolved_at: null,
    detection_delay_seconds: null,
    recovery_observed: null,
    recovery_seconds: null,
    evidence_useful: false,
  }),
];

function filterHosts(p: HostListParams): HostList {
  const page = p.page ?? 1;
  const size = p.page_size ?? 50;
  let items = HOSTS.filter(inScope(p.source));
  if (p.health?.length) items = items.filter((h) => p.health!.includes(h.health.status));
  const facets = {
    source: { live: 0, synthetic: 0, replay: 0 } as Record<Source, number>,
    health: { healthy: 0, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0 } as Record<HealthStatus, number>,
  };
  for (const h of HOSTS) {
    facets.source[h.source] += 1;
    facets.health[h.health.status] += 1;
  }
  return {
    items: items.slice((page - 1) * size, page * size),
    page,
    page_size: size,
    total: items.length,
    facets,
    thresholds: THRESHOLDS,
  };
}

const notInDemo = (what: string) => async () => {
  throw new Error(`${what} is not part of the read-only landing preview.`);
};

/** The read-only API used by the landing page's product showcase. It implements every read the showcase screens make. */
export function createDemoApi(): ApiClient {
  const api: Partial<ApiClient> = {
    me: async () => ({ user_id: "demo", authorized: true }),
    hosts: async (p: HostListParams = {}) => filterHosts(p),
    host: async (id: string): Promise<HostDetail> => {
      const h = byId(id);
      if (!h) throw new Error("Demonstration host not found");
      return { ...h, metrics: metricsFor(h) };
    },
    latest: async (id: string): Promise<LatestMetrics> => {
      const h = byId(id);
      if (!h) throw new Error("Demonstration host not found");
      return { host_id: h.host_id, source: h.source, last_seen: h.last_seen, metrics: metricsFor(h) };
    },
    series: async (id, p): Promise<SeriesResponse> => {
      const h = byId(id);
      if (!h) throw new Error("Demonstration host not found");
      const n = 60;
      const seed = Number(h.host_id.slice(-2));
      const base = h.latest.cpu_percent ?? 20;
      const values = trace(seed, base, 6, n);
      const end = p.end ? Date.parse(p.end) : NOW;
      const start = p.start ? Date.parse(p.start) : end - 60 * 60 * SEC;
      const step = (end - start) / n;
      return {
        host_id: h.host_id,
        source: h.source,
        metric: p.metric,
        kind: "gauge",
        start: iso(start),
        end: iso(end),
        step_seconds: Math.round(step / SEC),
        agg: (p.agg as SeriesResponse["agg"]) ?? "avg",
        series: [
          {
            labels: {},
            unit: "%",
            value_type: "gauge",
            points: values.map((v, k) => ({ t: iso(start + k * step), value: v, samples: 4 })),
          },
        ],
      };
    },
    alerts: async (status: AlertStatusFilter = "active", _signal, limit): Promise<AlertList> => {
      const keep = (a: Alert) =>
        status === "all" ? true : status === "resolved" ? a.state === "resolved" : a.state !== "resolved";
      const alerts = ALERTS.filter(keep).slice(0, limit ?? 50);
      return { alerts, total: ALERTS.filter(keep).length };
    },
    alertHistogram: async (p): Promise<AlertHistogram> => {
      const start = Date.parse(p.start);
      const end = Date.parse(p.end);
      const buckets = bucketCounts(start, end, p.bucket_seconds);
      const critical = buckets.reduce((n, b) => n + b.critical, 0);
      const warning = buckets.reduce((n, b) => n + b.warning, 0);
      return {
        start: p.start,
        end: p.end,
        bucket_seconds: p.bucket_seconds,
        sources: p.source ?? ["live", "synthetic", "replay"],
        buckets,
        total: critical + warning,
        critical,
        warning,
        complete: true,
      };
    },
    distribution: async (p: { metric: DistributionMetric; source?: Source[] }): Promise<Distribution> => {
      const pool = HOSTS.filter(inScope(p.source));
      const value = (h: Host) => (p.metric === "memory" ? h.latest.memory_percent : p.metric === "disk_max" ? h.latest.disk_percent_max : h.latest.cpu_percent);
      const buckets = [0, 0, 0, 0, 0];
      let noData = 0;
      for (const h of pool) {
        const v = value(h);
        if (v === null) { noData += 1; continue; }
        const k = Math.min(4, Math.floor(v / 20));
        buckets[k] = (buckets[k] ?? 0) + 1;
      }
      const top = pool
        .filter((h) => value(h) !== null)
        .sort((a, b) => (value(b) ?? 0) - (value(a) ?? 0))[0];
      return {
        metric: p.metric,
        sources: p.source ?? ["live", "synthetic", "replay"],
        total_in_scope: pool.length,
        bucket_labels: ["0–20", "20–40", "40–60", "60–80", "80–100"],
        buckets,
        no_data: noData,
        states: {
          normal: pool.filter((h) => h.health.status === "healthy").length,
          warning: pool.filter((h) => h.health.status === "warning").length,
          critical: pool.filter((h) => h.health.status === "critical").length,
          none: pool.filter((h) => h.health.status === "offline" || h.health.status === "stale").length,
        },
        max: top ? { value: value(top) ?? 0, host_id: top.host_id, hostname: top.hostname } : null,
        complete: true,
      };
    },
    rankings: async (p: { metric: RankedMetric; source?: Source[]; limit?: number }): Promise<Ranking> => {
      const pool = HOSTS.filter(inScope(p.source));
      const value = (h: Host) => (p.metric === "system.memory.utilization" ? h.latest.memory_percent : h.latest.cpu_percent);
      const ranked = pool
        .filter((h) => value(h) !== null)
        .sort((a, b) => (value(b) ?? 0) - (value(a) ?? 0));
      const limit = p.limit ?? 8;
      return {
        metric: p.metric,
        sources: p.source ?? ["live", "synthetic", "replay"],
        total_in_scope: pool.length,
        with_value: ranked.length,
        without_value: pool.length - ranked.length,
        limit,
        complete: true,
        items: ranked.slice(0, limit).map((h) => ({
          host_id: h.host_id,
          hostname: h.hostname,
          source: h.source,
          value: value(h) ?? 0,
          observed_at: h.last_seen,
          last_seen_age_seconds: h.last_seen_age_seconds,
          reporting_status: h.reporting_status,
        })),
      };
    },
    infrastructure: async () => ({
      environments: [],
      unassigned_regions: [],
      unassigned_clusters: [],
      ungrouped_hosts: [],
      services: [],
      unassigned_services: [],
    }) as unknown as Awaited<ReturnType<ApiClient["infrastructure"]>>,
    platformHealth: async () => ({ http: 200, status: "ok", app: "ok", database: "ok" }),
    rehearsalCatalogue: async () => ({ scenarios: [REHEARSAL_SCENARIO] }),
    rehearsalTargets: async (): Promise<RehearsalTarget[]> =>
      HOSTS.filter((h) => h.source === "synthetic").map((h) => ({
        host_id: h.host_id,
        kind: "synthetic",
        display_name: h.hostname,
        telemetry_state: h.reporting_status === "reporting" ? "reporting" : "silent",
        eligible: h.reporting_status === "reporting",
        reason: h.reporting_status === "reporting" ? null : "The host is not reporting.",
      })),
    rehearsals: async () => ({ rehearsals: REHEARSALS }),
    rehearsal: async (id: string) => {
      const r = REHEARSALS.find((x) => x.id === id);
      if (!r) throw new Error("Demonstration rehearsal not found");
      return r;
    },
    startRehearsal: notInDemo("Starting a rehearsal"),
    stopRehearsal: notInDemo("Stopping a rehearsal"),
    acknowledgeAlert: notInDemo("Acknowledging an alert"),
    resolveAlert: notInDemo("Resolving an alert"),
  };
  return api as ApiClient;
}

