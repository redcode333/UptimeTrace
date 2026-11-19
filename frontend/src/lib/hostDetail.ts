/**
 * Host Detail logic: pure functions, no React. Thresholds come only from the configured settings. A missing rule is reported as
 * missing; no default is substituted here, so the page never judges a value against a limit the operator did not configure.
 */
import type { Alert, AlertSeverity } from "../api/types";

export type MetricRules = Record<string, { warning: number; critical: number }>;

/** The configured limits for one metric, or undefined when the configuration does not contain it. */
export function ruleFor(rules: MetricRules | undefined, metric: string): { warning: number; critical: number } | undefined {
  return rules?.[metric];
}

/** "warning ≥ 85% · critical ≥ 95%", or null when there is no configured rule to show. */
export function thresholdText(rule: { warning: number; critical: number } | undefined): string | null {
  return rule ? `warning ≥ ${rule.warning}% · critical ≥ ${rule.critical}%` : null;
}

const SEVERITY_RANK: Record<AlertSeverity, number> = { critical: 0, warning: 1 };

export interface HostAlerts {
  active: Alert[];
  resolved: Alert[];
  /** Resolved alerts beyond the shown limit. */
  resolvedHidden: number;
}

/**
 * Split one host's alerts. Active: critical first, then the newest. Resolved: the most recently resolved first.
 * Input must come from a host-filtered request; this function does not infer host membership.
 */
export function splitHostAlerts(alerts: readonly Alert[], resolvedLimit = 10): HostAlerts {
  const active = alerts
    .filter((a) => a.state !== "resolved")
    .sort((x, y) => SEVERITY_RANK[x.severity] - SEVERITY_RANK[y.severity] || y.opened_at.localeCompare(x.opened_at) || x.id.localeCompare(y.id));
  const resolvedAll = alerts
    .filter((a) => a.state === "resolved")
    .sort((x, y) => (y.resolved_at ?? "").localeCompare(x.resolved_at ?? "") || x.id.localeCompare(y.id));
  return { active, resolved: resolvedAll.slice(0, resolvedLimit), resolvedHidden: Math.max(0, resolvedAll.length - resolvedLimit) };
}

/** How a resolved alert ended. Recovery and manual closure are never confused. */
export function resolutionText(a: Alert): string {
  if (a.resolution === "recovered") return "Recovered by telemetry";
  if (a.resolution === "manual") return a.resolved_by ? `Resolved manually by ${a.resolved_by}` : "Resolved manually";
  return "Resolved (how it ended was not recorded)";
}

const METRIC_NAME: Record<string, string> = {
  "system.cpu.utilization": "CPU utilisation",
  "system.memory.utilization": "Memory utilisation",
  "system.disk.utilization": "Disk utilisation",
};

/** The triggering condition in words. Host offline alerts name no metric. */
export function conditionText(a: Pick<Alert, "rule" | "metric" | "labels">): string {
  if (a.rule === "host_offline") return "Host stopped reporting telemetry";
  const name = (a.metric && METRIC_NAME[a.metric]) || a.metric || "Metric";
  return a.labels.mount ? `${name} (${a.labels.mount})` : name;
}

/** Acknowledgement state for an alert that is still open. */
export function acknowledgementText(a: Alert): string | null {
  if (a.state === "open") return "Not acknowledged";
  if (a.state === "acknowledged") return a.acknowledged_by ? `Acknowledged by ${a.acknowledged_by}` : "Acknowledged";
  return null;
}

/** Whether the alert list was truncated, so the host's alerts may be incomplete. */
export function alertsCapped(loaded: number, total: number): boolean {
  return loaded < total;
}
