import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { ApiError } from "../../api/client";
import type { Rehearsal } from "../../api/rehearsalTypes";
import { fakeApi, renderApp } from "../../test/harness";
import { RehearsalDetailPage } from "./RehearsalDetailPage";

const ID = "11111111-1111-4111-8111-111111111111";

const rehearsal = (over: Partial<Rehearsal> = {}): Rehearsal => ({
  id: ID, host_id: "sim-web-1", scenario: "cpu_saturation", metric: "system.cpu.utilization", execution: "COMPLETED",
  execution_reason: "The full plan was accepted, the fault was confirmed in stored telemetry, and recovery telemetry was accepted.",
  detection: "DETECTED", detection_reason: "A CPU alert opened 0.2 s after the confirmed fault onset, within the 60 s budget.",
  created_by: "user_test_1", created_at: "2026-11-11T12:00:00Z", started_at: "2026-11-11T12:00:00Z", ended_at: "2026-11-11T12:02:30Z",
  last_progress_at: "2026-11-11T12:02:30Z", stop_requested_at: null, stop_requested_by: null, phase: null, planned_envelopes: 11,
  sent_envelopes: 11, accepted_envelopes: 11, failed_envelopes: 0, fault_sent: 6, fault_accepted: 6, recovery_accepted: 3,
  fault_confirmed_at: "2026-11-11T12:00:30.200Z", fault_observed_value: 96.4, fault_ended_at: "2026-11-11T12:01:45Z",
  recovery_sent_at: "2026-11-11T12:01:55Z", cleanup_status: "not_needed", alert_id: "33333333-3333-4333-8333-333333333333",
  alert_opened_at: "2026-11-11T12:00:30.400Z", alert_resolved_at: "2026-11-11T12:02:00Z", detection_delay_seconds: 0.2,
  recovery_observed: true, recovery_seconds: 5, evidence_useful: true, evidence_points: 7, error_class: null, config: {},
  steps: [
    { seq: 1, phase: "baseline", sent_at: "2026-11-11T12:00:00Z", outcome: "accepted", error_class: null, observed_value: 24.1, observed_at: "2026-11-11T11:59:59.5Z" },
    { seq: 4, phase: "fault", sent_at: "2026-11-11T12:00:30Z", outcome: "accepted", error_class: null, observed_value: 96.4, observed_at: "2026-11-11T12:00:30.2Z" },
  ],
  ...over,
});

function detail(api: Parameters<typeof fakeApi>[0] = {}) {
  return renderApp(
    <Routes>
      <Route path="/rehearsal/:rehearsalId" element={<RehearsalDetailPage />} />
    </Routes>,
    { route: `/rehearsal/${ID}`, api: fakeApi(api) },
  );
}

describe("rehearsal detail", () => {
  it("shows execution and detection as two separate verdicts", async () => {
    detail({ rehearsal: async () => rehearsal() });
    const exec = await screen.findByRole("heading", { name: "Execution" });
    const det = screen.getByRole("heading", { name: "Detection" });
    const execCard = exec.closest("section")!;
    const detCard = det.closest("section")!;
    expect(within(execCard).getByText("✓ COMPLETED")).toBeInTheDocument();
    expect(within(detCard).getByText("✓ DETECTED")).toBeInTheDocument();
    expect(screen.getByText("Detection delay").nextElementSibling).toHaveTextContent("0.2 s");
  });

  it("a failed injection is shown as FAILED with no detection credit", async () => {
    detail({
      rehearsal: async () => rehearsal({
        execution: "FAILED", execution_reason: "Injection failed: no fault telemetry was accepted by UptimeTrace.",
        detection: "INCONCLUSIVE", detection_reason: "No fault was confirmed in stored telemetry, so detection cannot be judged.",
        fault_confirmed_at: null, fault_observed_value: null, alert_id: null, alert_opened_at: null, detection_delay_seconds: null,
        recovery_observed: null,
      }),
    });
    const execCard = (await screen.findByRole("heading", { name: "Execution" })).closest("section")!;
    const detCard = screen.getByRole("heading", { name: "Detection" }).closest("section")!;
    expect(within(execCard).getByText("■ FAILED")).toBeInTheDocument();
    expect(within(detCard).getByText("? INCONCLUSIVE")).toBeInTheDocument();
    expect(screen.queryByText("✓ DETECTED")).not.toBeInTheDocument();
    expect(screen.getByText("Not confirmed in stored telemetry")).toBeInTheDocument();
  });

  it("a missing detection is UNDETECTED and links to no alert", async () => {
    detail({
      rehearsal: async () => rehearsal({
        detection: "UNDETECTED", detection_reason: "The fault was confirmed in telemetry, but no CPU alert opened for it.",
        alert_id: null, alert_opened_at: null, alert_resolved_at: null, detection_delay_seconds: null, recovery_observed: null,
      }),
    });
    const detCard = (await screen.findByRole("heading", { name: "Detection" })).closest("section")!;
    expect(within(detCard).getByText("■ UNDETECTED")).toBeInTheDocument();
    expect(screen.getByText("No alert opened for this rehearsal")).toBeInTheDocument();
  });

  it("the telemetry that was sent is listed with the CPU value read back from storage", async () => {
    detail({ rehearsal: async () => rehearsal() });
    const table = await screen.findByRole("table");
    expect(within(table).getByText("96.4%")).toBeInTheDocument();
    expect(within(table).getByText("Fault")).toBeInTheDocument();
  });

  it("a running rehearsal shows progress and offers Stop, which needs a second confirmation", async () => {
    const stopRehearsal = vi.fn(async () => rehearsal({ execution: "RUNNING", stop_requested_at: "2026-11-11T12:00:45Z", stop_requested_by: "user_test_1" }));
    detail({ rehearsal: async () => rehearsal({ execution: "RUNNING", detection: null, detection_reason: null, ended_at: null }), stopRehearsal });
    fireEvent.click(await screen.findByRole("button", { name: "Stop rehearsal" }));
    expect(screen.getByRole("group", { name: "Confirm stopping the rehearsal" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm stop" }));
    await waitFor(() => expect(stopRehearsal).toHaveBeenCalledWith(ID));
  });

  it("Keep running cancels the stop without calling the API", async () => {
    const stopRehearsal = vi.fn();
    detail({ rehearsal: async () => rehearsal({ execution: "RUNNING", detection: null, ended_at: null }), stopRehearsal });
    fireEvent.click(await screen.findByRole("button", { name: "Stop rehearsal" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep running" }));
    expect(stopRehearsal).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Stop rehearsal" })).toBeInTheDocument();
  });

  it("once a stop is requested the page says so and offers no second stop", async () => {
    detail({ rehearsal: async () => rehearsal({ execution: "RUNNING", detection: null, ended_at: null, stop_requested_at: "2026-11-11T12:00:45Z", stop_requested_by: "user_test_1" }) });
    expect(await screen.findByText(/stop requested, ending the fault/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop rehearsal" })).not.toBeInTheDocument();
  });

  it("a failed stop is reported in words", async () => {
    detail({
      rehearsal: async () => rehearsal({ execution: "RUNNING", detection: null, ended_at: null }),
      stopRehearsal: async () => { throw new ApiError(0, "network_error", "Could not reach the UptimeTrace API."); },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Stop rehearsal" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm stop" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the UptimeTrace API");
  });

  it("an unknown rehearsal is reported as not found", async () => {
    detail({ rehearsal: async () => { throw new ApiError(404, "rehearsal_not_found", "No rehearsal with that id."); } });
    expect(await screen.findByText("Not found")).toBeInTheDocument();
  });
});

describe("memory rehearsal display", () => {
  it("names memory, not CPU, in the fault onset and the telemetry table", async () => {
    detail({
      rehearsal: async () => rehearsal({
        scenario: "memory_saturation", metric: "system.memory.utilization", fault_observed_value: 85.7,
        detection_reason: "A Memory alert opened 0.2 s after the confirmed fault onset, within the 60 s budget.",
        steps: [{ seq: 4, phase: "fault", sent_at: "2026-11-11T12:00:30Z", outcome: "accepted", error_class: null, observed_value: 85.7, observed_at: "2026-11-11T12:00:30.2Z" }],
      }),
    });
    expect(await screen.findByText(/Memory 85\.7% in stored telemetry/)).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: "Memory read back" })).toBeInTheDocument();
    expect(within(table).getByText("85.7%")).toBeInTheDocument();
    expect(screen.queryByText(/CPU/)).not.toBeInTheDocument();
  });
});
