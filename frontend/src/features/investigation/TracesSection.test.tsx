import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import { ApiError } from "../../api/client";
import type { Investigation, InvestigationTraces, TraceDetail } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { InvestigationPage } from "./InvestigationPage";

const ID = "11111111-1111-4111-8111-111111111111";
const TRACE = "a".repeat(32);
const noRecord = () => Promise.reject(new ApiError(404, "report_not_found", "none"));

const traces = (over: Partial<InvestigationTraces> = {}): InvestigationTraces => ({
  available: true, reason: null, window_start: "2026-11-11T11:40:00Z", window_end: "2026-11-11T12:20:00Z",
  services: ["checkout"], truncated: false,
  items: [{
    trace_id: TRACE, started_at: "2026-11-11T12:05:00Z", duration_ms: 2400, root: { service: "checkout", operation: "GET /checkout" },
    services: ["checkout", "inventory"], span_count: 2, error_count: 1, slow_span_count: 2,
    slowest: { service: "inventory", operation: "inventory.query", duration_ms: 2200 },
  }],
  ...over,
});

const detail: TraceDetail = {
  summary: traces().items[0]!, truncated: false,
  spans: [
    { span_id: "s1", parent_span_id: null, service: "checkout", operation: "GET /checkout", offset_ms: 0, duration_ms: 2400, error: false, slow: true },
    { span_id: "s2", parent_span_id: "s1", service: "inventory", operation: "inventory.query", offset_ms: 1, duration_ms: 2200, error: true, slow: true },
  ],
};

function investigation(over: Partial<Investigation> = {}): Investigation {
  return {
    as_of: "2026-11-11T12:10:00Z", window_minutes: 30,
    alert: { id: ID, host_id: "api-1", state: "open", severity: "critical", rule: "threshold", metric: "system.cpu.utilization", labels: {},
      condition_key: "k", acknowledged_by: null, acknowledged_at: null, resolved_by: null, resolution: null, resolved_at: null,
      opened_at: "2026-11-11T12:00:00Z", last_evaluated_at: "2026-11-11T12:10:00Z" } as Investigation["alert"],
    trigger: { rule: "threshold", metric: "system.cpu.utilization", labels: {}, severity: "critical", comparison: ">=", threshold: 95,
      trigger_value: 97, trigger_observed_at: "2026-11-11T12:00:00Z", first_fired_at: "2026-11-11T12:00:00Z",
      last_evaluated_at: "2026-11-11T12:10:00Z", unavailable: [] } as unknown as Investigation["trigger"],
    recovery: { recovered: false, resolution: null, resolved_by: null, resolved_at: null, value: null, observed_at: null },
    host: { host_id: "api-1", known: true, current: null }, series: [], series_note: null, events: [],
    related: { window_minutes: 30, total: 0, limit: 20, offset: 0, note: "", items: [] },
    settings: { version: 0, warning_limit: 85, critical_limit: 95, stale_after_seconds: 90, offline_after_seconds: 300 },
    traces: traces(),
    ...over,
  } as unknown as Investigation;
}

function renderPage(api: ReturnType<typeof fakeApi>) {
  return renderApp(<Routes><Route path="/alerts/:alertId" element={<InvestigationPage />} /></Routes>, { api, route: `/alerts/${ID}` });
}

describe("Application traces in an investigation", () => {
  it("lists the window's traces with their root, duration, errors and slowest span", async () => {
    renderPage(fakeApi({ investigation: async () => investigation(), investigationReport: noRecord }));
    const section = await screen.findByRole("region", { name: "Application traces" });
    expect(within(section).getByText("GET /checkout", { exact: false })).toBeInTheDocument();
    expect(within(section).getByText(/2400 ms/)).toBeInTheDocument();
    expect(within(section).getByText(/1 error span/)).toBeInTheDocument();
    expect(within(section).getByText(/Slowest: inventory · inventory.query \(2200 ms\)/)).toBeInTheDocument();
  });

  it("opens one trace and shows its spans, with errors named in words", async () => {
    const api = fakeApi({ investigation: async () => investigation(), investigationReport: noRecord, trace: async () => detail });
    renderPage(api);
    const section = await screen.findByRole("region", { name: "Application traces" });
    fireEvent.click(within(section).getByRole("button", { name: "Inspect trace aaaaaaaa" }));
    const table = await screen.findByRole("table", { name: "Spans of trace aaaaaaaa" });
    expect(within(table).getByText("inventory.query")).toBeInTheDocument();
    expect(within(table).getByText("error, slow")).toBeInTheDocument();
  });

  it.each([
    ["not_configured", /not connected to this deployment/],
    ["no_registered_services", /No registered service runs on this host/],
    ["unavailable", /could not be reached/],
  ] as const)("explains %s in plain words and leaves the rest of the investigation intact", async (reason, text) => {
    renderPage(fakeApi({ investigation: async () => investigation({ traces: traces({ available: false, reason, items: [], services: [] }) }),
      investigationReport: noRecord }));
    const section = await screen.findByRole("region", { name: "Application traces" });
    expect(within(section).getByText(text)).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "What triggered it" })).toBeInTheDocument();
  });

  it("says when the window has no traces, without an empty decorative panel", async () => {
    renderPage(fakeApi({ investigation: async () => investigation({ traces: traces({ items: [] }) }), investigationReport: noRecord }));
    const section = await screen.findByRole("region", { name: "Application traces" });
    expect(within(section).getByText(/No traces from this host's services in the window/)).toBeInTheDocument();
  });
});
