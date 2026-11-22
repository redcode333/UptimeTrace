import type { Alert, AlertHistogram, Distribution, Host, Ranking, RankedMetric, Source } from "../api/types";

/**
 * Test doubles shaped to the server contracts of GET /api/v1/hosts/rankings and GET /api/v1/alerts/histogram. The contracts
 * themselves are verified against real PostgreSQL in backend/tests/test_command_center_data.py; these fakes only let the UI be tested.
 */

const ALL: Source[] = ["live", "synthetic", "replay"];

function sourceOf(h: Host): Source {
  return h.source;
}

export function rankingFake(hosts: Host[], metric: RankedMetric, limit = 8, sources?: Source[]): Ranking {
  const scope = hosts.filter((h) => !sources || sources.includes(sourceOf(h)));
  const valueOf = (h: Host) =>
    h.reporting_status === "reporting" ? (metric === "system.cpu.utilization" ? h.latest.cpu_percent : h.latest.memory_percent) : null;
  const ranked = scope
    .map((h) => ({ h, v: valueOf(h) }))
    .filter((r): r is { h: Host; v: number } => r.v !== null)
    .sort((a, b) => b.v - a.v || a.h.hostname.localeCompare(b.h.hostname));
  return {
    metric,
    sources: sources ?? ALL,
    total_in_scope: scope.length,
    with_value: ranked.length,
    without_value: scope.length - ranked.length,
    limit,
    complete: true,
    items: ranked.slice(0, limit).map(({ h, v }) => ({
      host_id: h.host_id,
      hostname: h.hostname,
      source: sourceOf(h),
      value: v,
      observed_at: h.last_seen,
      last_seen_age_seconds: h.last_seen_age_seconds,
      reporting_status: h.reporting_status,
    })),
  };
}

/** Buckets on UTC epoch multiples, the window ends exclusive, every bucket present. Mirrors the server's rule. */
export function histogramFake(
  alerts: Alert[],
  p: { start: string; end: string; bucket_seconds: number; source?: Source[] },
  total?: { critical: number; warning: number },
): AlertHistogram {
  const step = p.bucket_seconds;
  const startMs = Date.parse(p.start);
  const endMs = Date.parse(p.end);
  const first = Math.floor(startMs / 1000 / step) * step;
  const count = Math.ceil((endMs / 1000 - first) / step);
  const buckets = Array.from({ length: count }, (_, i) => ({
    start: new Date((first + i * step) * 1000).toISOString(),
    critical: 0,
    warning: 0,
  }));
  for (const a of alerts) {
    const t = Date.parse(a.opened_at);
    if (t < startMs || t >= endMs) continue;
    const i = Math.floor((t / 1000 - first) / step);
    if (a.severity === "critical") buckets[i]!.critical += 1;
    else buckets[i]!.warning += 1;
  }
  const crit = total ? total.critical : buckets.reduce((n, b) => n + b.critical, 0);
  const warn = total ? total.warning : buckets.reduce((n, b) => n + b.warning, 0);
  return {
    start: p.start,
    end: p.end,
    bucket_seconds: step,
    sources: p.source ?? ALL,
    buckets,
    total: crit + warn,
    critical: crit,
    warning: warn,
    complete: true,
  };
}

const ATTENTION_RANK: Record<string, number> = { critical: 0, warning: 1, stale: 2, offline: 3, unknown: 4, healthy: 5 };

/** The server's attention order: severity first, then hostname. */
export function attentionFakeOrder(hosts: Host[]): Host[] {
  return [...hosts].sort(
    (a, b) => (ATTENTION_RANK[a.health.status] ?? 9) - (ATTENTION_RANK[b.health.status] ?? 9) || a.hostname.localeCompare(b.hostname),
  );
}

const DIST_EDGES: Array<[number, number]> = [[0, 20], [20, 40], [40, 60], [60, 80], [80, 101]];

/** Mirrors GET /api/v1/hosts/distribution: every in-scope host counted once, missing values in no_data. */
export function distributionFake(
  hosts: Host[],
  metric: "cpu" | "memory" | "disk_max",
  rules: Record<string, { warning: number; critical: number }>,
  sources?: Source[],
): Distribution {
  const ruleName = metric === "cpu" ? "system.cpu.utilization" : metric === "memory" ? "system.memory.utilization" : "system.disk.utilization";
  const rule = rules[ruleName];
  const scope = hosts.filter((h) => !sources || sources.includes(h.source));
  const valueOf = (h: Host): number | null =>
    metric === "cpu" ? h.latest.cpu_percent : metric === "memory" ? h.latest.memory_percent : h.latest.disk_percent_max;
  const buckets = DIST_EDGES.map(() => 0);
  const states = { normal: 0, warning: 0, critical: 0, none: 0 };
  let noData = 0;
  let max: Distribution["max"] = null;
  for (const h of scope) {
    const v = valueOf(h);
    if (v === null || v === undefined) {
      noData++;
      states.none++;
      continue;
    }
    const idx = DIST_EDGES.findIndex(([lo, hi]) => v >= lo && v < hi);
    buckets[idx < 0 ? 0 : idx]!++;
    if (!rule) states.none++;
    else if (v >= rule.critical) states.critical++;
    else if (v >= rule.warning) states.warning++;
    else states.normal++;
    if (!max || v > max.value || (v === max.value && h.hostname.localeCompare(max.hostname) < 0)) {
      max = { value: v, host_id: h.host_id, hostname: h.hostname };
    }
  }
  return {
    metric,
    sources: sources ?? ALL,
    total_in_scope: scope.length,
    bucket_labels: ["0–20", "20–40", "40–60", "60–80", "80–100"],
    buckets,
    no_data: noData,
    states,
    max,
    complete: true,
  };
}
