import { describe, expect, it } from "vitest";
import type { Rehearsal, RehearsalTarget } from "../api/rehearsalTypes";
import { confirmationMatches, detectionTone, evidenceCopy, executionTone, formatSeconds, progressPercent, targetLabel } from "./rehearsal";

const base: Rehearsal = {
  id: "11111111-1111-4111-8111-111111111111", host_id: "sim-web-1", scenario: "cpu_saturation", metric: "system.cpu.utilization", execution: "COMPLETED",
  execution_reason: "The full plan was accepted.", detection: "DETECTED", detection_reason: "A CPU alert opened 0.2 s after onset.",
  created_by: "user_test_1", created_at: "2026-11-11T12:00:00Z", started_at: "2026-11-11T12:00:00Z", ended_at: "2026-11-11T12:02:30Z",
  last_progress_at: "2026-11-11T12:02:30Z", stop_requested_at: null, stop_requested_by: null, phase: null, planned_envelopes: 11,
  sent_envelopes: 11, accepted_envelopes: 11, failed_envelopes: 0, fault_sent: 6, fault_accepted: 6, recovery_accepted: 3,
  fault_confirmed_at: "2026-11-11T12:00:30Z", fault_observed_value: 96, fault_ended_at: "2026-11-11T12:01:45Z",
  recovery_sent_at: "2026-11-11T12:01:55Z", cleanup_status: "not_needed", alert_id: "a", alert_opened_at: "2026-11-11T12:00:30Z",
  alert_resolved_at: "2026-11-11T12:02:00Z", detection_delay_seconds: 0.2, recovery_observed: true, recovery_seconds: 5,
  evidence_useful: true, evidence_points: 7, error_class: null, config: {},
};

describe("execution and detection tones", () => {
  it("a failed injection reads as failed, never as a success", () => {
    expect(executionTone("FAILED")).toBe("bad");
    expect(detectionTone("INCONCLUSIVE")).toBe("warn");
  });
  it("an undetected fault is a bad detection, a detected one is ok", () => {
    expect(detectionTone("UNDETECTED")).toBe("bad");
    expect(detectionTone("DETECTED")).toBe("ok");
    expect(detectionTone(null)).toBe("neutral");
  });
});

describe("progress and time", () => {
  it("progress is the share of planned envelopes sent, capped at 100", () => {
    expect(progressPercent({ sent_envelopes: 5, planned_envelopes: 11 })).toBe(45);
    expect(progressPercent({ sent_envelopes: 14, planned_envelopes: 11 })).toBe(100);
    expect(progressPercent({ sent_envelopes: 0, planned_envelopes: 0 })).toBe(0);
  });
  it("durations are human and never invent a value for a missing one", () => {
    expect(formatSeconds(0.25)).toBe("0.3 s");
    expect(formatSeconds(150)).toBe("2 min 30 s");
    expect(formatSeconds(null)).toBe("—");
  });
});

describe("confirmation and copy", () => {
  it("the typed host id must match exactly", () => {
    expect(confirmationMatches("sim-web-1", "sim-web-1")).toBe(true);
    expect(confirmationMatches("sim-web-1", "sim-web-")).toBe(false);
    expect(confirmationMatches("sim-web-1", "SIM-WEB-1")).toBe(false);
    expect(confirmationMatches("", "")).toBe(false);
  });
  it("a target label includes the display name when there is one", () => {
    const t: RehearsalTarget = { host_id: "sim-a", kind: "synthetic", display_name: "Lab A", telemetry_state: "reporting", eligible: true, reason: null };
    expect(targetLabel(t)).toBe("Lab A (sim-a)");
    expect(targetLabel({ ...t, display_name: null })).toBe("sim-a");
  });
  it("evidence copy says when there is no alert and when the check did not complete", () => {
    expect(evidenceCopy({ ...base, alert_id: null })).toMatch(/No alert was opened/);
    expect(evidenceCopy({ ...base, evidence_useful: null })).toMatch(/did not complete/);
    expect(evidenceCopy(base)).toContain("7 telemetry point");
  });
});
