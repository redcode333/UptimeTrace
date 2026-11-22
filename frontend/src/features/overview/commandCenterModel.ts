import type { Alert, Host, HealthStatus } from "../../api/types";
import { metricDef } from "../../lib/metrics";

/**
 * Pure transformations for the Command Center panels. Every value comes from a host record or an alert record; nothing here
 * creates a metric, a forecast or a state that the backend did not report.
 */

/** Tone used by the map and the tiles. Design colours: normal, warning, critical, offline; stale and unknown are neutral. */
export type Tone = "normal" | "warning" | "critical" | "offline" | "neutral";

export function toneOf(status: HealthStatus): Tone {
  switch (status) {
    case "healthy": return "normal";
    case "warning": return "warning";
    case "critical": return "critical";
    case "offline": return "offline";
    case "stale":
    case "unknown":
      return "neutral";
  }
}

export interface HexCell {
  host: Host;
  x: number;
  y: number;
  tone: Tone;
  /** CPU now: the reported value, only while the agent is reporting. Used only for the 3D column height. */
  cpu: number | null;
}

/** Hosts laid out in a hexagonal grid, sorted by hostname so the map does not reshuffle between refreshes. */
export function hexLayout(hosts: Host[], cols: number, size = 19): HexCell[] {
  const w = Math.sqrt(3) * size;
  const sorted = [...hosts].sort((a, b) => a.hostname.localeCompare(b.hostname));
  return sorted.map((host, i) => {
    const row = Math.floor(i / cols);
    const col = i % cols;
    return {
      host,
      x: 14 + w / 2 + col * w + (row % 2 ? w / 2 : 0),
      y: 22 + row * size * 1.5,
      tone: toneOf(host.health.status),
      cpu: host.reporting_status === "reporting" ? host.latest.cpu_percent : null,
    };
  });
}

/** Bucket size for a time window, so every window draws about 12 to 56 bars. */
export function bucketMsFor(windowMs: number): number {
  const MIN = 60_000;
  if (windowMs <= 60 * MIN) return 5 * MIN;
  if (windowMs <= 6 * 60 * MIN) return 15 * MIN;
  if (windowMs <= 24 * 60 * MIN) return 30 * MIN;
  return 180 * MIN;
}

export interface Insight {
  id: string;
  tone: "critical" | "warning" | "neutral";
  title: string;
  body: string;
  meta: string;
}

/**
 * Evidence-backed insight cards. Only facts the backend reported: open alerts, and hosts whose agent is not reporting.
 * The outlook card states that no risk score is served; it never shows a score.
 */
export function insightsFrom(activeAlerts: Alert[], hosts: Host[], hostname: (id: string) => string | undefined): Insight[] {
  const out: Insight[] = [];
  const critical = activeAlerts.filter((a) => a.severity === "critical" && a.state !== "resolved");
  for (const a of critical.slice(0, 3)) {
    const host = hostname(a.host_id) ?? a.host_id;
    out.push({
      id: `alert-${a.id}`,
      tone: "critical",
      title: `Rule fired · ${a.metric ? (metricDef(a.metric)?.label ?? a.metric) : "threshold"}`,
      body: `${host} is past its configured limit${a.value !== null && a.threshold !== null ? ` (${a.value} against ${a.threshold})` : ""}.`,
      meta: `opened ${new Date(a.opened_at).toISOString().slice(11, 16)} UTC`,
    });
  }
  const silent = hosts.filter((h) => h.reporting_status === "silent" || h.health.status === "offline");
  for (const h of silent.slice(0, 2)) {
    out.push({
      id: `silent-${h.host_id}`,
      tone: "warning",
      title: "No report",
      body: `${h.hostname} has not reported for ${Math.round(h.last_seen_age_seconds / 60)} minutes.`,
      meta: `last seen ${new Date(h.last_seen).toISOString().slice(11, 16)} UTC`,
    });
  }
  out.push({
    id: "outlook",
    tone: "neutral",
    title: "Outlook · not available",
    body: "The experimental risk model is not served yet, so no forecast is shown.",
    meta: "Risk Outlook",
  });
  return out;
}
