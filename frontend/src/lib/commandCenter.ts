/**
 * Command Center logic: pure functions, no React. Exact-versus-partial rules are decided here and tested directly.
 *
 * Coverage rules (docs/FRONTEND_ARCHITECTURE.md section 6.4):
 *  - A list is capped when the rows returned are fewer than the exact total the API reports.
 *  - Facet counts are exact at any fleet size (computed server-side over the whole fleet).
 *  - A scope-filtered alert count is exact only when the alert list is complete, and, when a subset of sources is selected,
 *    every alert's host resolves in a complete host list. Otherwise it is marked partial and never shown as a total.
 */
import type { Alert, AlertSeverity, Host, HostList, Source, Thresholds } from "../api/types";

export const TOP_ALERTS = 5;
export const ALL_SOURCES: readonly Source[] = ["live", "synthetic", "replay"];

export interface Coverage {
  loaded: number;
  total: number;
  complete: boolean;
}

/** `total` is the exact count the API reports; `loaded` is the number of rows actually returned. */
export function coverage(loaded: number, total: number): Coverage {
  return { loaded, total, complete: loaded >= total };
}

export function hostCoverage(list: Pick<HostList, "items" | "total"> | undefined): Coverage | null {
  return list ? coverage(list.items.length, list.total) : null;
}

/** Index the loaded hosts by id. Used only to read each host's source for the alert join. */
export function indexHosts(hosts: readonly Host[]): Map<string, Host> {
  return new Map(hosts.map((h) => [h.host_id, h]));
}

export function isAllSelected(selected: readonly Source[]): boolean {
  return ALL_SOURCES.every((s) => selected.includes(s));
}

export interface AlertSummary {
  /** Exact: the API's total of active alerts across every source. */
  totalActive: number;
  /** Rows actually loaded. */
  loaded: number;
  /** The alert list returned fewer rows than its total. */
  alertsCapped: boolean;
  /** A subset of sources is selected, so the host join is needed. */
  joinNeeded: boolean;
  /** Loaded alerts whose host is not in the loaded host list (only counted when the join is needed). */
  notLoaded: number;
  /** Scope-filtered counts are exact only when this is true. */
  scopeExact: boolean;
  /** Loaded alerts that belong to the selected scope. */
  inScope: Alert[];
  bySeverity: Record<AlertSeverity, number>;
  byRule: { threshold: number; host_offline: number };
  /** The most severe open alerts in scope: critical first, then newest. */
  top: Alert[];
}

/**
 * Summarise active alerts for the selected scope.
 * `hostIndex` must come from the all-sources host list when a subset is selected; it is ignored when every source is selected.
 */
export function summarizeAlerts(
  alerts: readonly Alert[],
  totalActive: number,
  selected: readonly Source[],
  hostIndex: Map<string, Host> | null,
  hostsComplete: boolean,
): AlertSummary {
  const joinNeeded = !isAllSelected(selected);
  const alertsCapped = alerts.length < totalActive;
  let notLoaded = 0;
  const inScope: Alert[] = [];
  for (const a of alerts) {
    if (!joinNeeded) {
      inScope.push(a);
      continue;
    }
    const src = hostIndex?.get(a.host_id)?.source;
    if (src === undefined) notLoaded++;
    else if (selected.includes(src)) inScope.push(a);
  }
  const scopeExact = !alertsCapped && (!joinNeeded || (hostsComplete && notLoaded === 0));
  const bySeverity: Record<AlertSeverity, number> = { critical: 0, warning: 0 };
  const byRule = { threshold: 0, host_offline: 0 };
  for (const a of inScope) {
    if (a.severity === "critical" || a.severity === "warning") bySeverity[a.severity]++;
    if (a.rule === "threshold" || a.rule === "host_offline") byRule[a.rule]++;
  }
  const top = [...inScope]
    .sort((x, y) => (x.severity === y.severity ? 0 : x.severity === "critical" ? -1 : 1) || y.opened_at.localeCompare(x.opened_at) || x.id.localeCompare(y.id))
    .slice(0, TOP_ALERTS);
  return { totalActive, loaded: alerts.length, alertsCapped, joinNeeded, notLoaded, scopeExact, inScope, bySeverity, byRule, top };
}

export interface ConnectedSummary {
  /** LIVE hosts in the workspace (exact). */
  live: number;
  /** Fresh and judged: healthy, warning or critical. */
  reporting: number;
  silent: number;
  stale: number;
  unknown: number;
}

/** From the facets of a `source=live` host query (health facet respects the source filter, so these are LIVE-only counts). */
export function connectedSummary(list: Pick<HostList, "facets">): ConnectedSummary {
  const h = list.facets.health;
  return {
    live: list.facets.source.live,
    reporting: h.healthy + h.warning + h.critical,
    silent: h.offline,
    stale: h.stale,
    unknown: h.unknown,
  };
}

/** Human reason shown in the attention table. Missing values are described, never guessed. */
export function reasonText(h: Host): string {
  const ageNoSuffix = (s: number) => reasonAge(s);
  if (h.health.status === "stale") return `Telemetry ${ageNoSuffix(h.last_seen_age_seconds)} old`;
  if (h.health.status === "offline") return `No telemetry for ${ageNoSuffix(h.last_seen_age_seconds)}`;
  if (h.health.status === "unknown") return "No fresh CPU, memory or disk measurement";
  const r = h.health.reasons[0];
  if (!r) return "—";
  const name = r.metric.replace("system.", "").replace(".utilization", "");
  const mount = r.labels.mount ? ` ${r.labels.mount}` : "";
  const more = h.health.reasons.length > 1 ? ` +${h.health.reasons.length - 1} more` : "";
  return `${name}${mount} ${r.value.toFixed(1)}% ≥ ${r.threshold}%${more}`;
}

function reasonAge(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

/** Health order used for attention: most severe first. */
export const ATTENTION_ORDER = ["critical", "warning", "stale", "offline", "unknown", "healthy"] as const;

/** Sort loaded attention rows by health severity, then hostname. Applies to the loaded rows only. */
export function sortAttention(hosts: readonly Host[]): Host[] {
  const rank = (s: string) => ATTENTION_ORDER.indexOf(s as (typeof ATTENTION_ORDER)[number]);
  return [...hosts].sort((a, b) => rank(a.health.status) - rank(b.health.status) || a.hostname.localeCompare(b.hostname));
}

export type MetricState = "normal" | "warning" | "critical" | "none";

/**
 * State of one measurement against the configured thresholds (the same rule the health states use).
 * Missing values are "none", never normal. Missing thresholds yield "none" as well: nothing is judged without a limit.
 */
export function metricState(value: number | null | undefined, rule: { warning: number; critical: number } | undefined): MetricState {
  if (value == null || !Number.isFinite(value) || !rule) return "none";
  if (value >= rule.critical) return "critical";
  if (value >= rule.warning) return "warning";
  return "normal";
}

export interface StateTally {
  normal: number;
  warning: number;
  critical: number;
  none: number;
}

export function tallyStates(values: ReadonlyArray<number | null | undefined>, rule: { warning: number; critical: number } | undefined): StateTally {
  const t: StateTally = { normal: 0, warning: 0, critical: 0, none: 0 };
  for (const v of values) t[metricState(v, rule)]++;
  return t;
}

export function thresholdRule(thresholds: Thresholds | undefined, metric: string): { warning: number; critical: number } | undefined {
  return thresholds?.rules[metric];
}
