/**
 * Alert transitions derivable from the alert list response (docs/FRONTEND_ARCHITECTURE.md section 6.5). Only four kinds exist:
 *   OPENED                  from opened_at
 *   ACKNOWLEDGED            from acknowledged_at, actor acknowledged_by
 *   RECOVERED               from resolved_at when resolution = recovered (system recovery)
 *   RESOLVED_MANUALLY       from resolved_at when resolution = manual, actor resolved_by
 * Severity changes, agent and host state changes are not in the response and are never derived here.
 */
import type { Alert, AlertSeverity } from "../api/types";

export type TransitionKind = "OPENED" | "ACKNOWLEDGED" | "RECOVERED" | "RESOLVED_MANUALLY";

export const TRANSITION_LABEL: Record<TransitionKind, string> = {
  OPENED: "Opened",
  ACKNOWLEDGED: "Acknowledged",
  RECOVERED: "Recovered",
  RESOLVED_MANUALLY: "Resolved manually",
};

export interface Transition {
  id: string;
  kind: TransitionKind;
  /** ISO timestamp of the transition. */
  at: string;
  alertId: string;
  hostId: string;
  severity: AlertSeverity;
  /** Who did it. Null when the response does not say (shown as an em dash, never invented). */
  actor: string | null;
}

export const DEFAULT_WINDOW_MS = 24 * 60 * 60 * 1000;
export const DEFAULT_TRANSITION_LIMIT = 20;

const KIND_RANK: Record<TransitionKind, number> = { OPENED: 0, ACKNOWLEDGED: 1, RECOVERED: 2, RESOLVED_MANUALLY: 2 };

/** The transitions one alert row proves. A resolved alert without a resolution is omitted: it is not labelled. */
export function transitionsOf(a: Alert): Transition[] {
  const base = { alertId: a.id, hostId: a.host_id, severity: a.severity };
  const out: Transition[] = [{ id: `${a.id}:OPENED`, kind: "OPENED", at: a.opened_at, actor: null, ...base }];
  if (a.acknowledged_at) {
    out.push({ id: `${a.id}:ACKNOWLEDGED`, kind: "ACKNOWLEDGED", at: a.acknowledged_at, actor: a.acknowledged_by, ...base });
  }
  if (a.resolved_at && a.resolution === "recovered") {
    out.push({ id: `${a.id}:RECOVERED`, kind: "RECOVERED", at: a.resolved_at, actor: a.resolved_by ?? "system", ...base });
  } else if (a.resolved_at && a.resolution === "manual") {
    out.push({ id: `${a.id}:RESOLVED_MANUALLY`, kind: "RESOLVED_MANUALLY", at: a.resolved_at, actor: a.resolved_by, ...base });
  }
  return out;
}

/**
 * Transitions inside the window ending at `now`, newest first, at most `limit`.
 * `alerts` may be a capped list: the caller decides whether the result is complete (see `transitionsComplete`).
 */
export function recentTransitions(
  alerts: readonly Alert[],
  now: Date,
  windowMs: number = DEFAULT_WINDOW_MS,
  limit: number = DEFAULT_TRANSITION_LIMIT,
): Transition[] {
  const from = now.getTime() - windowMs;
  const all = alerts.flatMap(transitionsOf).filter((t) => {
    const ms = Date.parse(t.at);
    return Number.isFinite(ms) && ms >= from && ms <= now.getTime();
  });
  // Newest first. Equal instants: the later lifecycle step first (recovery, then acknowledgement, then opening).
  all.sort((x, y) => Date.parse(y.at) - Date.parse(x.at) || KIND_RANK[y.kind] - KIND_RANK[x.kind] || x.id.localeCompare(y.id));
  return all.slice(0, limit);
}

/**
 * Whether the transition list can claim it is complete. A capped alert list never can: an alert older than the loaded rows may
 * have been resolved inside the window, and it would be missing. Only a complete list supports an empty-window claim.
 */
export function transitionsComplete(loaded: number, total: number): boolean {
  return loaded >= total;
}
