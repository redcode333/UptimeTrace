import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { ApiError } from "../../api/client";
import type { Investigation, InvestigationRecord } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { InvestigationPage } from "./InvestigationPage";

const ID = "11111111-1111-4111-8111-111111111111";

const investigation = (over: Partial<Investigation> = {}): Investigation => ({
  as_of: "2026-11-11T12:10:00Z",
  window_minutes: 30,
  alert: {
    id: ID, host_id: "web-1", state: "resolved", severity: "critical", rule: "threshold", metric: "system.cpu.utilization",
    labels: {}, condition_key: "system.cpu.utilization|{}", acknowledged_by: "user_test_1", acknowledged_at: "2026-11-11T12:01:00Z",
    resolved_by: "system", resolution: "recovered",
  },
  trigger: {
    rule: "threshold", metric: "system.cpu.utilization", labels: {}, severity: "critical", comparison: ">=", threshold: 95,
    trigger_value: 97, trigger_observed_at: "2026-11-11T11:59:59.500Z", first_fired_at: "2026-11-11T12:00:00Z",
    last_evaluated_at: "2026-11-11T12:10:00Z", unavailable: [],
  },
  recovery: { recovered: true, resolution: "recovered", resolved_by: "system", resolved_at: "2026-11-11T12:10:00Z", value: 20, observed_at: "2026-11-11T12:09:59.500Z" },
  host: { host_id: "web-1", known: true, current: { health: { status: "healthy" }, reporting_status: "reporting", last_seen: "2026-11-11T12:10:00Z", source: "live" } },
  series: [{
    window: "around_trigger", start: "2026-11-11T11:30:00Z", end: "2026-11-11T12:30:00Z", step_seconds: 60, metric: "system.cpu.utilization", labels: {},
    points: [
      { t: "2026-11-11T11:59:00Z", value: 22, samples: 1 },
      { t: "2026-11-11T12:00:00Z", value: 97, samples: 1 },
      { t: "2026-11-11T12:01:00Z", value: null, samples: 0 },
    ],
  }],
  series_note: null,
  events: [
    { event: "opened", occurred_at: "2026-11-11T12:00:00Z", actor: "system", detail: { value: 97, threshold: 95 } },
    { event: "acknowledged", occurred_at: "2026-11-11T12:01:00Z", actor: "user_test_1", detail: {} },
    { event: "recovered", occurred_at: "2026-11-11T12:10:00Z", actor: "system", detail: { value: 20 } },
  ],
  related: {
    window_minutes: 30, total: 1, limit: 20, offset: 0, note: "Related alerts share a host and overlap in time. That is correlation only.",
    items: [{ id: "22222222-2222-4222-8222-222222222222", rule: "threshold", metric: "system.memory.utilization", labels: {}, severity: "warning",
      state: "resolved", opened_at: "2026-11-11T12:00:00Z", resolution: "recovered" }],
  },
  settings: { version: 0, warning_limit: 85, critical_limit: 95, stale_after_seconds: 90, offline_after_seconds: 300 },
  ...over,
});

const record = (over: Partial<InvestigationRecord> = {}): InvestigationRecord => ({
  id: "33333333-3333-4333-8333-333333333333", alert_id: ID, created_at: "2026-11-11T12:11:00Z", created_by: "user_test_1",
  schema_version: "investigation.v1", evidence_digest: "a".repeat(64), digest_matches: true, rederivation_matches: true, verified: true,
  body: {
    generated_at: "2026-11-11T12:11:00Z", generated_by: "user_test_1",
    facts: [{ id: "F1", text: "Trigger: CPU utilisation read 97.00 against the >= 95.00 critical limit.", source: "trigger" }],
    conclusions: [{ id: "C1", text: "The trigger condition was met.", basis: ["F1"] }],
    not_established: ["Root cause is not established."],
  },
  ...over,
});

function renderPage(api: ReturnType<typeof fakeApi>) {
  return renderApp(
    <Routes><Route path="/alerts/:alertId" element={<InvestigationPage />} /></Routes>,
    { api, route: `/alerts/${ID}` },
  );
}

const noRecord = () => Promise.reject(new ApiError(404, "report_not_found", "none"));

describe("InvestigationPage", () => {
  it("states the exact trigger, its observed value, the limit and the observation time", async () => {
    renderPage(fakeApi({ investigation: async () => investigation(), investigationReport: noRecord }));
    const trigger = await screen.findByRole("region", { name: "What triggered it" });
    expect(within(trigger).getByText("97.0%")).toBeInTheDocument();
    expect(within(trigger).getByText(/>= 95.0%/)).toBeInTheDocument();
    expect(within(trigger).getByText(/\(critical\)/)).toBeInTheDocument();
    expect(within(trigger).getByText("Threshold")).toBeInTheDocument();
  });

  it("marks evidence that was never recorded as not recorded, and never invents it", async () => {
    const missing = investigation({
      trigger: { ...investigation().trigger, trigger_value: null, trigger_observed_at: null, unavailable: ["trigger_value", "trigger_observed_at"] },
    });
    renderPage(fakeApi({ investigation: async () => missing, investigationReport: noRecord }));
    const trigger = await screen.findByRole("region", { name: "What triggered it" });
    expect(within(trigger).getAllByText("Not recorded").length).toBeGreaterThanOrEqual(2);
    expect(within(trigger).queryByText("97.0%")).not.toBeInTheDocument();
    expect(within(trigger).getByText(/never inferred/)).toBeInTheDocument();
  });

  it("shows recovery by telemetry with the recovery value and its observation time", async () => {
    renderPage(fakeApi({ investigation: async () => investigation(), investigationReport: noRecord }));
    const rec = await screen.findByRole("region", { name: "Recovery" });
    expect(within(rec).getByText("Yes, by telemetry")).toBeInTheDocument();
    expect(within(rec).getByText("20.0%")).toBeInTheDocument();
  });

  it("says a manual close is not telemetry recovery", async () => {
    const manual = investigation({
      alert: { ...investigation().alert, state: "resolved", resolution: "manual", resolved_by: "user_test_1" },
      recovery: { recovered: false, resolution: "manual", resolved_by: "user_test_1", resolved_at: "2026-11-11T12:05:00Z", value: null, observed_at: null },
    });
    renderPage(fakeApi({ investigation: async () => manual, investigationReport: noRecord }));
    const rec = await screen.findByRole("region", { name: "Recovery" });
    expect(within(rec).getByText(/does not show that the condition cleared/)).toBeInTheDocument();
  });

  it("lists the lifecycle in order and the related alert on the same host with the correlation caveat", async () => {
    renderPage(fakeApi({ investigation: async () => investigation(), investigationReport: noRecord }));
    const life = await screen.findByRole("list", { name: "Alert lifecycle" });
    const items = within(life).getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items[0]).toMatch(/Opened/);
    expect(items[1]).toMatch(/Acknowledged/);
    expect(items[2]).toMatch(/Recovered/);
    const related = screen.getByRole("region", { name: "Related alerts on this host" });
    expect(within(related).getByRole("link", { name: "Memory utilisation" })).toBeInTheDocument();
    expect(within(related).getByText(/correlation only/)).toBeInTheDocument();
  });

  it("states that telemetry gaps are gaps and that the offline host has no metric series", async () => {
    const offline = investigation({
      alert: { ...investigation().alert, rule: "host_offline", metric: null }, trigger: { ...investigation().trigger, rule: "host_offline", metric: null, trigger_value: null, threshold: null, comparison: null },
      series: [], series_note: "Host offline alerts have no metric series; the host's last telemetry time is the evidence.",
    });
    renderPage(fakeApi({ investigation: async () => offline, investigationReport: noRecord }));
    expect(await screen.findByText(/Host offline alerts have no metric series/)).toBeInTheDocument();
  });

  it("refetches the view for a different telemetry window", async () => {
    const investigationFn = vi.fn(async (_id: string, params: { window_minutes?: number }) => investigation({ window_minutes: params.window_minutes ?? 30 }));
    renderPage(fakeApi({ investigation: investigationFn, investigationReport: noRecord }));
    await screen.findByRole("region", { name: "What triggered it" });
    fireEvent.click(screen.getByRole("button", { name: "±60 min" }));
    await waitFor(() => expect(investigationFn).toHaveBeenLastCalledWith(ID, { window_minutes: 60 }, expect.anything()));
  });

  it("creates the record on request and then shows what it verified", async () => {
    const create = vi.fn(async () => record());
    renderPage(fakeApi({ investigation: async () => investigation(), investigationReport: noRecord, createInvestigationReport: create }));
    fireEvent.click(await screen.findByRole("button", { name: "Create investigation record" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/verified: the stored evidence reproduces these conclusions/)).toBeInTheDocument();
    expect(screen.getByText("Root cause is not established.")).toBeInTheDocument();
  });

  it("shows the stored record on a later visit, without offering to create another", async () => {
    renderPage(fakeApi({ investigation: async () => investigation(), investigationReport: async () => record() }));
    expect(await screen.findByText(/verified: the stored evidence reproduces these conclusions/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create investigation record" })).not.toBeInTheDocument();
  });

  it("warns, not reassures, when a stored record fails verification", async () => {
    renderPage(fakeApi({ investigation: async () => investigation(), investigationReport: async () => record({ verified: false, digest_matches: false }) }));
    expect(await screen.findByText(/Verification failed/)).toBeInTheDocument();
  });

  it("says so when the alert does not exist in this workspace", async () => {
    renderPage(fakeApi({
      investigation: () => Promise.reject(new ApiError(404, "alert_not_found", "No alert with that id.")),
      investigationReport: noRecord,
    }));
    expect(await screen.findByText("No such alert")).toBeInTheDocument();
  });
});
