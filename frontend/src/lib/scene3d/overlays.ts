/**
 * Health, alert and provenance overlays. They are drawn over a model, never baked into it, and every one carries a text
 * label and a glyph so state is not conveyed by colour alone (glyphs match components/ui/Badges.tsx).
 */
import type { Alert, Source } from "../../api/types";
import { RULE_TEXT, STATE_ORDER, tallyNodes, topSeverity, type Tally, type TopoNode } from "../topology";
import type { SceneState } from "./types";

export interface HealthOverlay {
  state: SceneState;
  label: string;
  glyph: string;
  color: string;
  /** Animation of the status ring: critical pulses fast, warning slowly, others are still. */
  pulse: "none" | "slow" | "fast";
  /** Stale, offline and unloaded models are drawn dimmed: their figures are not current. */
  dim: boolean;
}

const HEALTH: Record<SceneState, Omit<HealthOverlay, "state">> = {
  healthy: { label: "HEALTHY", glyph: "●", color: "#35d0b5", pulse: "none", dim: false },
  warning: { label: "WARNING", glyph: "▲", color: "#e9b8ff", pulse: "slow", dim: false },
  critical: { label: "CRITICAL", glyph: "■", color: "#ff6a55", pulse: "fast", dim: false },
  stale: { label: "STALE", glyph: "◐", color: "#8aa3b8", pulse: "none", dim: true },
  offline: { label: "OFFLINE", glyph: "○", color: "#8aa3b8", pulse: "none", dim: true },
  unknown: { label: "UNKNOWN", glyph: "?", color: "#7f98ad", pulse: "none", dim: false },
  unloaded: { label: "NO DATA LOADED", glyph: "–", color: "#4d6378", pulse: "none", dim: true },
};

export function healthOverlay(state: SceneState): HealthOverlay {
  return { state, ...HEALTH[state] };
}

export interface AlertItem {
  id: string;
  severity: Alert["severity"];
  rule: string;
  metric: string | null;
  value: number | null;
  threshold: number | null;
  /** The existing investigation page for this alert. */
  href: string;
}

export interface AlertOverlay {
  severity: Alert["severity"];
  glyph: string;
  color: string;
  count: number;
  items: AlertItem[];
}

/** An alert marker for exactly the host the alerts belong to. Null when there is no active alert. */
export function alertOverlay(alerts: readonly Alert[]): AlertOverlay | null {
  const active = alerts.filter((a) => a.state !== "resolved");
  const sev = topSeverity(active);
  if (!sev) return null;
  const items = [...active]
    .sort((a, b) => (a.severity === b.severity ? a.opened_at.localeCompare(b.opened_at) : a.severity === "critical" ? -1 : 1))
    .map((a) => ({
      id: a.id,
      severity: a.severity,
      rule: RULE_TEXT[a.rule],
      metric: a.metric,
      value: a.value,
      threshold: a.threshold,
      href: `/alerts/${encodeURIComponent(a.id)}`,
    }));
  return { severity: sev, glyph: sev === "critical" ? "■" : "▲", color: sev === "critical" ? "#ff6a55" : "#e9b8ff", count: items.length, items };
}

export interface ProvenanceOverlay {
  source: Source;
  label: "LIVE" | "SYNTHETIC" | "REPLAY";
  /** Ring pattern under the model: the non-colour cue, matching the badge borders. */
  pattern: "solid" | "dashed" | "dotted";
  color: string;
  /** Only live hosts count as connected infrastructure. */
  isLive: boolean;
  description: string;
}

const PROVENANCE: Record<Source, ProvenanceOverlay> = {
  live: { source: "live", label: "LIVE", pattern: "solid", color: "#35d0b5", isLive: true, description: "Connected infrastructure" },
  synthetic: { source: "synthetic", label: "SYNTHETIC", pattern: "dashed", color: "#a3b8c8", isLive: false, description: "Simulated host, not real infrastructure" },
  replay: { source: "replay", label: "REPLAY", pattern: "dotted", color: "#4aa3ff", isLive: false, description: "Replayed history, not current" },
};

export const provenanceOverlay = (source: Source): ProvenanceOverlay => PROVENANCE[source];

/**
 * Figures for a group (environment, region, cluster, unassigned group). A group has no health of its own; these are
 * explicitly labelled aggregates of what its hosts report.
 */
export interface GroupAggregate {
  kind: "aggregate";
  label: string;
  hosts: number;
  tally: Tally;
  alerts: number;
  /** Short line, e.g. "Aggregate of 4 hosts: critical 1 · healthy 3". */
  summary: string;
}

export function groupAggregate(nodes: readonly TopoNode[]): GroupAggregate {
  const t = tallyNodes(nodes);
  const parts = STATE_ORDER.filter((s) => t.tally[s] > 0).map((s) => `${s === "unloaded" ? "no data" : s} ${t.tally[s]}`);
  const label = `Aggregate of ${t.hosts} host${t.hosts === 1 ? "" : "s"}`;
  return { kind: "aggregate", label, hosts: t.hosts, tally: t.tally, alerts: t.alerts, summary: parts.length ? `${label}: ${parts.join(" · ")}` : label };
}
