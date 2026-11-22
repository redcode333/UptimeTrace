import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { ApiError } from "../../api/client";
import type { Rehearsal, RehearsalScenario, RehearsalTarget } from "../../api/rehearsalTypes";
import { fakeApi, renderApp } from "../../test/harness";
import { RehearsalPage } from "./RehearsalPage";

const SIM: RehearsalTarget = { host_id: "sim-web-1", kind: "synthetic", display_name: null, telemetry_state: "reporting", eligible: true, reason: null };
const LIVE: RehearsalTarget = {
  host_id: "web-prod-1", kind: "live", display_name: null, telemetry_state: "reporting", eligible: false,
  reason: "Only registered synthetic hosts can be targeted. This host is 'live'. Rehearsals never touch live or replayed hosts.",
};
const SCENARIO: RehearsalScenario = {
  id: "cpu_saturation", title: "CPU saturation on a synthetic host", summary: "Normal, then pinned high, then normal.", metric: "system.cpu.utilization", metric_label: "CPU",
  affects: "Telemetry only. No process, load or CPU is changed on any machine.", interval_seconds: 15, planned_envelopes: 11,
  expected_duration_seconds: 150, phases: [{ name: "baseline", ticks: 2 }, { name: "fault", ticks: 6 }, { name: "recovery", ticks: 3 }],
  limits: { max_envelopes: 20, consecutive_failure_limit: 3, detection_budget_seconds: 60 },
};

const running = (over: Partial<Rehearsal> = {}): Rehearsal => ({
  id: "11111111-1111-4111-8111-111111111111", host_id: "sim-web-1", scenario: "cpu_saturation", metric: "system.cpu.utilization", execution: "RUNNING", execution_reason: null,
  detection: null, detection_reason: null, created_by: "user_test_1", created_at: "2026-11-11T12:00:00Z", started_at: "2026-11-11T12:00:00Z",
  ended_at: null, last_progress_at: "2026-11-11T12:00:30Z", stop_requested_at: null, stop_requested_by: null, phase: "fault",
  planned_envelopes: 11, sent_envelopes: 5, accepted_envelopes: 5, failed_envelopes: 0, fault_sent: 3, fault_accepted: 3,
  recovery_accepted: 0, fault_confirmed_at: null, fault_observed_value: null, fault_ended_at: null, recovery_sent_at: null,
  cleanup_status: "not_needed", alert_id: null, alert_opened_at: null, alert_resolved_at: null, detection_delay_seconds: null,
  recovery_observed: null, recovery_seconds: null, evidence_useful: null, evidence_points: null, error_class: null, config: {},
  ...over,
});

function page(over: Parameters<typeof fakeApi>[0] = {}) {
  return renderApp(
    <Routes>
      <Route path="/rehearsal" element={<RehearsalPage />} />
      <Route path="/rehearsal/:rehearsalId" element={<p>DETAIL PAGE</p>} />
    </Routes>,
    {
      route: "/rehearsal",
      api: fakeApi({
        rehearsalTargets: async () => [SIM, LIVE],
        rehearsalCatalogue: async () => ({ scenarios: [SCENARIO] }),
        rehearsals: async () => ({ rehearsals: [] }),
        ...over,
      }),
    },
  );
}

describe("Failure Rehearsal start flow", () => {
  it("a live host is listed but cannot be selected, and says why", async () => {
    page();
    const live = await screen.findByRole("radio", { name: /web-prod-1/ });
    expect(live).toBeDisabled();
    expect(screen.getByText(/Rehearsals never touch live or replayed hosts/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /sim-web-1/ })).toBeEnabled();
  });

  it("the run button stays disabled until the host, scenario, acknowledgement and exact typed id are all given", async () => {
    const startRehearsal = vi.fn(async () => running());
    page({ startRehearsal });
    fireEvent.click(await screen.findByRole("radio", { name: /sim-web-1/ }));
    fireEvent.click(screen.getByRole("radio", { name: /CPU saturation/ }));
    const run = screen.getByRole("button", { name: "Start rehearsal" });
    expect(run).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(run).toBeDisabled();
    const typed = screen.getByRole("textbox");
    fireEvent.change(typed, { target: { value: "SIM-WEB-1" } });
    expect(run).toBeDisabled();
    fireEvent.change(typed, { target: { value: "sim-web-1" } });
    expect(run).toBeEnabled();
    fireEvent.click(run);
    await waitFor(() => expect(startRehearsal).toHaveBeenCalledWith({ host_id: "sim-web-1", scenario: "cpu_saturation", confirm_host_id: "sim-web-1" }));
  });

  it("shows the safety check: what is affected and how long it runs", async () => {
    page();
    fireEvent.click(await screen.findByRole("radio", { name: /sim-web-1/ }));
    fireEvent.click(screen.getByRole("radio", { name: /CPU saturation/ }));
    expect(screen.getByText(/No process, load or CPU is changed on any machine/)).toBeInTheDocument();
    expect(screen.getByText(/2 min 30 s/)).toBeInTheDocument();
  });

  it("a refused start is explained and nothing is shown as started", async () => {
    const startRehearsal = vi.fn(async () => { throw new ApiError(409, "rehearsal_already_running", "A rehearsal is already running."); });
    page({ startRehearsal });
    fireEvent.click(await screen.findByRole("radio", { name: /sim-web-1/ }));
    fireEvent.click(screen.getByRole("radio", { name: /CPU saturation/ }));
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "sim-web-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Start rehearsal" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A rehearsal is already running");
    expect(screen.queryByText("DETAIL PAGE")).not.toBeInTheDocument();
  });

  it("a running rehearsal blocks a second start and is linked from the top of the page", async () => {
    page({ rehearsals: async () => ({ rehearsals: [running()] }) });
    expect(await screen.findByText(/A REHEARSAL IS RUNNING/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("radio", { name: /sim-web-1/ }));
    fireEvent.click(screen.getByRole("radio", { name: /CPU saturation/ }));
    expect(screen.getByText(/cannot start yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start rehearsal" })).toBeDisabled();
  });

  it("recent rehearsals show execution and detection in separate columns", async () => {
    const done = running({ execution: "COMPLETED", detection: "DETECTED", detection_delay_seconds: 0.2, phase: null, id: "22222222-2222-4222-8222-222222222222" });
    page({ rehearsals: async () => ({ rehearsals: [done] }) });
    const table = await screen.findByRole("table");
    expect(within(table).getByText("✓ COMPLETED")).toBeInTheDocument();
    expect(within(table).getByText("✓ DETECTED")).toBeInTheDocument();
  });

  it("with no registered hosts it says so rather than showing an empty form", async () => {
    page({ rehearsalTargets: async () => [] });
    expect(await screen.findByText("No registered hosts")).toBeInTheDocument();
  });
});

describe("scenario choices", () => {
  it("the safety check names the measured metric and states the synthetic-only boundary", async () => {
    page({ rehearsalCatalogue: async () => ({ scenarios: [{ ...SCENARIO, id: "memory_saturation", title: "Memory saturation on a synthetic host", metric: "system.memory.utilization", metric_label: "Memory" }] }) });
    fireEvent.click(await screen.findByRole("radio", { name: /sim-web-1/ }));
    fireEvent.click(screen.getByRole("radio", { name: /Memory saturation/ }));
    expect(screen.getByText(/Memory utilisation/)).toBeInTheDocument();
    expect(screen.getByText(/Synthetic hosts only/)).toBeInTheDocument();
  });
});
