import type { Rehearsal, RehearsalDetection, RehearsalExecution, RehearsalTarget } from "../api/rehearsalTypes";

/** Display rules for failure rehearsals. Every status carries a text label and a shape, never colour alone. */

export const EXECUTION_LABEL: Record<RehearsalExecution, string> = {
  RUNNING: "● RUNNING", COMPLETED: "✓ COMPLETED", FAILED: "■ FAILED", STOPPED: "◼ STOPPED", INCONCLUSIVE: "? INCONCLUSIVE",
};

export const DETECTION_LABEL: Record<RehearsalDetection, string> = {
  DETECTED: "✓ DETECTED", LATE_DETECTION: "▲ LATE DETECTION", UNDETECTED: "■ UNDETECTED", INCONCLUSIVE: "? INCONCLUSIVE",
};

/** A failed injection is never a detection, so a FAILED execution always reads as a failed rehearsal regardless of detection. */
export function executionTone(e: RehearsalExecution): "ok" | "bad" | "warn" | "neutral" {
  if (e === "COMPLETED") return "ok";
  if (e === "FAILED") return "bad";
  if (e === "RUNNING") return "neutral";
  return "warn"; // STOPPED and INCONCLUSIVE both need the reader to look at the reason
}

export function detectionTone(d: RehearsalDetection | null): "ok" | "bad" | "warn" | "neutral" {
  if (d === null) return "neutral";
  if (d === "DETECTED") return "ok";
  if (d === "UNDETECTED") return "bad";
  return "warn"; // LATE_DETECTION and INCONCLUSIVE
}

export const PHASE_LABEL: Record<string, string> = { baseline: "Baseline", fault: "Fault", recovery: "Recovery", cleanup: "Cleanup" };

export function isRunning(r: Pick<Rehearsal, "execution">): boolean {
  return r.execution === "RUNNING";
}

/** Share of the planned envelopes sent so far, 0..100. */
export function progressPercent(r: Pick<Rehearsal, "sent_envelopes" | "planned_envelopes">): number {
  if (r.planned_envelopes <= 0) return 0;
  return Math.min(100, Math.round((r.sent_envelopes / r.planned_envelopes) * 100));
}

export function formatSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined || Number.isNaN(s)) return "—";
  if (Math.abs(s) < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(Math.abs(s) / 60);
  const rest = Math.round(Math.abs(s) - m * 60);
  return `${s < 0 ? "−" : ""}${m} min ${rest} s`;
}

export function targetLabel(t: RehearsalTarget): string {
  return t.display_name ? `${t.display_name} (${t.host_id})` : t.host_id;
}

/** The typed confirmation: the host id, exactly. Case and whitespace matter, so a near miss is not accepted. */
export function confirmationMatches(hostId: string, typed: string): boolean {
  return hostId.length > 0 && typed === hostId;
}

export function evidenceCopy(r: Rehearsal): string {
  if (r.alert_id === null) return "No alert was opened, so there is no investigation evidence to review.";
  if (r.evidence_useful === null) return "The evidence check did not complete. Open the investigation to inspect the alert.";
  if (r.evidence_useful) return `The investigation holds the trigger and ${r.evidence_points ?? 0} telemetry point(s) around it.`;
  return "The investigation does not hold enough telemetry around the trigger to be useful.";
}
