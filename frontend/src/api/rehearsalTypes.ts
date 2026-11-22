/** Failure rehearsal contract (backend: app/rehearsal). Execution and detection are separate fields, never merged. */
export type RehearsalExecution = "RUNNING" | "COMPLETED" | "FAILED" | "STOPPED" | "INCONCLUSIVE";
export type RehearsalDetection = "DETECTED" | "LATE_DETECTION" | "UNDETECTED" | "INCONCLUSIVE";

export interface RehearsalScenario {
  id: string;
  title: string;
  summary: string;
  metric: string;
  metric_label: string;
  affects: string;
  interval_seconds: number;
  planned_envelopes: number;
  expected_duration_seconds: number;
  phases: { name: "baseline" | "fault" | "recovery"; ticks: number }[];
  limits: { max_envelopes: number; consecutive_failure_limit: number; detection_budget_seconds: number };
}

export interface RehearsalTarget {
  host_id: string;
  kind: "live" | "synthetic" | "replay";
  display_name: string | null;
  telemetry_state: string;
  eligible: boolean;
  reason: string | null;
}

export interface RehearsalStep {
  seq: number;
  phase: "baseline" | "fault" | "recovery" | "cleanup";
  sent_at: string;
  outcome: "accepted" | "rejected" | "failed";
  error_class: string | null;
  observed_value: number | null;
  observed_at: string | null;
}

export interface Rehearsal {
  id: string;
  host_id: string;
  scenario: string;
  metric: string | null;
  execution: RehearsalExecution;
  execution_reason: string | null;
  detection: RehearsalDetection | null;
  detection_reason: string | null;
  created_by: string;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  last_progress_at: string | null;
  stop_requested_at: string | null;
  stop_requested_by: string | null;
  phase: string | null;
  planned_envelopes: number;
  sent_envelopes: number;
  accepted_envelopes: number;
  failed_envelopes: number;
  fault_sent: number;
  fault_accepted: number;
  recovery_accepted: number;
  fault_confirmed_at: string | null;
  fault_observed_value: number | null;
  fault_ended_at: string | null;
  recovery_sent_at: string | null;
  cleanup_status: "not_needed" | "recovered" | "failed";
  alert_id: string | null;
  alert_opened_at: string | null;
  alert_resolved_at: string | null;
  detection_delay_seconds: number | null;
  recovery_observed: boolean | null;
  recovery_seconds: number | null;
  evidence_useful: boolean | null;
  evidence_points: number | null;
  error_class: string | null;
  config: { warning_limit?: number; critical_limit?: number; [key: string]: unknown };
  steps?: RehearsalStep[] | null;
}

export interface StartRehearsalIn {
  host_id: string;
  scenario: string; // a catalogue id; the server refuses anything else
  confirm_host_id: string;
}
