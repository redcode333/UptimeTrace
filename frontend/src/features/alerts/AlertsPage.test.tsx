import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { Alert, AlertDetail, AlertStatusFilter } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { AlertsPage, conditionText } from "./AlertsPage";

const base: Alert = {
  id: "11111111-1111-4111-8111-111111111111", host_id: "web-1", condition_key: "system.cpu.utilization|{}", rule: "threshold",
  metric: "system.cpu.utilization", labels: {}, severity: "critical", state: "open", value: 97.2, threshold: 95,
  opened_at: "2026-11-13T10:00:00Z", last_evaluated_at: "2026-11-13T10:01:00Z", acknowledged_at: null, acknowledged_by: null,
  resolved_at: null, resolved_by: null, resolution: null,
};
const offline: Alert = {
  ...base, id: "22222222-2222-4222-8222-222222222222", host_id: "db-1", rule: "host_offline", metric: null, condition_key: "host|offline",
  severity: "critical", value: null, threshold: null,
};
const detail = (a: Alert): AlertDetail => ({
  ...a,
  events: [
    { event: "opened", occurred_at: "2026-11-13T10:00:00Z", actor: "system", detail: { value: 97.2, threshold: 95, severity: "critical" } },
    { event: "severity_changed", occurred_at: "2026-11-13T10:00:30Z", actor: "system", detail: { from: "warning", to: "critical" } },
  ],
});

function alertsApi(opts: {
  alerts?: Alert[];
  byStatus?: Partial<Record<AlertStatusFilter, Alert[]>>;
  failList?: unknown;
  ack?: (id: string) => Promise<Alert>;
  resolve?: (id: string) => Promise<Alert>;
  statusCalls?: AlertStatusFilter[];
} = {}) {
  return fakeApi({
    alerts: async (status: AlertStatusFilter = "active") => {
      opts.statusCalls?.push(status);
      if (opts.failList) throw opts.failList;
      const items = opts.byStatus?.[status] ?? opts.alerts ?? [];
      return { alerts: items, total: items.length };
    },
    alert: async (id: string) => detail([base, offline].find((a) => a.id === id) ?? base),
    acknowledgeAlert: opts.ack ?? (async (id: string) => ({ ...base, id, state: "acknowledged", acknowledged_by: "user_test_1" })),
    resolveAlert: opts.resolve ?? (async (id: string) => ({ ...base, id, state: "resolved", resolution: "manual" })),
  });
}

describe("conditionText", () => {
  it("states the value that crossed the limit, in words", () => {
    expect(conditionText(base)).toBe("CPU utilisation 97.2%, at or above the critical limit of 95%");
  });
  it("names the disk mount when the alert is about one", () => {
    const disk = { ...base, metric: "system.disk.utilization", labels: { mount: "/data" }, severity: "warning" as const, value: 91, threshold: 90 };
    expect(conditionText(disk)).toBe("Disk utilisation (/data) 91.0%, at or above the warning limit of 90%");
  });
  it("describes a silent host without inventing a metric value", () => {
    expect(conditionText(offline)).toBe("Host has stopped reporting telemetry");
  });
});

describe("AlertsPage", () => {
  it("shows the active alerts with severity, host link, condition and state", async () => {
    renderApp(<AlertsPage />, { api: alertsApi({ alerts: [base, offline] }), route: "/alerts" });
    const row = (await screen.findByText("web-1")).closest("tr")!;
    expect(within(row).getByText("CRITICAL")).toBeTruthy();
    expect(within(row).getByText("CPU utilisation 97.2%, at or above the critical limit of 95%")).toBeTruthy();
    expect(within(row).getByText("Open")).toBeTruthy();
    expect(screen.getByRole("link", { name: "web-1" }).getAttribute("href")).toBe("/hosts/web-1");
    expect(screen.getByText("Host has stopped reporting telemetry")).toBeTruthy();
  });

  it("asks for the active list by default and switches the filter to resolved", async () => {
    const calls: AlertStatusFilter[] = [];
    renderApp(<AlertsPage />, { api: alertsApi({ alerts: [base], statusCalls: calls }), route: "/alerts" });
    await screen.findByText("web-1");
    expect(calls[0]).toBe("active");
    fireEvent.click(screen.getByRole("button", { name: "Resolved" }));
    await waitFor(() => expect(calls).toContain("resolved"));
    expect(screen.getByRole("button", { name: "Resolved" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("shows an honest empty state, not a healthy-looking table, when nothing is active", async () => {
    renderApp(<AlertsPage />, { api: alertsApi({ alerts: [] }), route: "/alerts" });
    expect(await screen.findByText("No active alerts")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("acknowledges an open alert through the API and shows the new state", async () => {
    const ack = vi.fn(async (id: string) => ({ ...base, id, state: "acknowledged" as const, acknowledged_by: "user_test_1" }));
    renderApp(<AlertsPage />, { api: alertsApi({ alerts: [base], ack }), route: "/alerts" });
    fireEvent.click(await screen.findByRole("button", { name: "Acknowledge alert for web-1" }));
    await waitFor(() => expect(ack).toHaveBeenCalledWith(base.id));
  });

  it("resolving asks for confirmation first and explains that a persisting breach re-opens", async () => {
    const resolve = vi.fn(async (id: string) => ({ ...base, id, state: "resolved" as const, resolution: "manual" as const }));
    renderApp(<AlertsPage />, { api: alertsApi({ alerts: [base], resolve }), route: "/alerts" });
    fireEvent.click(await screen.findByRole("button", { name: "Resolve alert for web-1" }));
    expect(screen.getByText(/If the breach is still there, a new alert opens/)).toBeTruthy();
    expect(resolve).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm resolve" }));
    await waitFor(() => expect(resolve).toHaveBeenCalledWith(base.id));
  });

  it("cancelling the resolve confirmation sends nothing", async () => {
    const resolve = vi.fn(async (id: string) => ({ ...base, id }));
    renderApp(<AlertsPage />, { api: alertsApi({ alerts: [base], resolve }), route: "/alerts" });
    fireEvent.click(await screen.findByRole("button", { name: "Resolve alert for web-1" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(resolve).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Confirm resolve" })).toBeNull();
  });

  it("shows the recovery timeline for an expanded alert", async () => {
    renderApp(<AlertsPage />, { api: alertsApi({ alerts: [base] }), route: "/alerts" });
    fireEvent.click(await screen.findByRole("button", { name: "Timeline for web-1" }));
    const timeline = await screen.findByRole("list", { name: "Alert timeline" });
    expect(within(timeline).getByText("Opened")).toBeTruthy();
    expect(within(timeline).getByText(/warning → critical/)).toBeTruthy();
  });

  it("explains a refused acknowledgement in words", async () => {
    const conflict = new ApiError(409, "alert_resolved", "This alert is already resolved.");
    renderApp(<AlertsPage />, { api: alertsApi({ alerts: [base], ack: async () => { throw conflict; } }), route: "/alerts" });
    fireEvent.click(await screen.findByRole("button", { name: "Acknowledge alert for web-1" }));
    expect(await screen.findByText(/already resolved, so it cannot be acknowledged/)).toBeTruthy();
  });

  it("reports an unreachable API as a failure, never as an empty or healthy list", async () => {
    renderApp(<AlertsPage />, { api: alertsApi({ failList: new ApiError(0, "network_error", "Could not reach the UptimeTrace API.") }), route: "/alerts" });
    expect(await screen.findByText("Could not reach the API")).toBeTruthy();
    expect(screen.queryByText("No active alerts")).toBeNull();
  });
});
