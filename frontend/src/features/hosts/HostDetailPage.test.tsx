import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { Alert, AlertList, AlertStatusFilter, MetricSample } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { detail, host, latestOf, sample, seriesOf } from "../../test/fixtures";
import { HostDetailPage } from "./HostDetailPage";

const h = host({
  host_id: "web-1", hostname: "WEB-1", service: "checkout",
  health: { status: "warning", category: "degraded", reasons: [{ metric: "system.memory.utilization", labels: {}, value: 88, threshold: 85, level: "warning", key: "system.memory.utilization|{}" }] },
});
const view = (
  <Routes><Route path="/hosts/:hostId" element={<HostDetailPage />} /></Routes>
);
type Over = Parameters<typeof fakeApi>[0];

function api(over: Over = {}) {
  return fakeApi({
    host: async () => detail(h),
    latest: async () => latestOf(h, [
      sample({ name: "system.cpu.utilization", value: 12.5 }),
      sample({ name: "system.disk.utilization", labels: { mount: "C:" }, value: 61 }),
      sample({ name: "system.disk.utilization", labels: { mount: "D:" }, value: 40, stale: true }),
      sample({ name: "system.network.rx_bytes", kind: "counter", unit: "bytes", labels: { interface: "eth0" }, value: 9_999_999_999, rate_per_second: 2_000_000 }),
      sample({ name: "system.network.tx_bytes", kind: "counter", unit: "bytes", labels: { interface: "eth0" }, value: 123_456_789, rate_per_second: null }),
    ]),
    series: async (_id, p) => seriesOf("web-1", p.metric, [1, 2, null, 4]),
    ...over,
  });
}

describe("Host detail: section tabs and outlook (Host detail board)", () => {
  it("offers real section links and shows Events and Incidents as unavailable, with the reason", async () => {
    renderApp(view, { api: api(), route: "/hosts/web-1" });
    const tabs = await screen.findByRole("navigation", { name: "Host sections" });
    expect(within(tabs).getByRole("link", { name: "Metrics" })).toHaveAttribute("href", expect.stringContaining("/metrics"));
    expect(within(tabs).getByRole("link", { name: "Alerts" })).toHaveAttribute("href", "#alerts");
    expect(within(tabs).getByRole("link", { name: "History" })).toHaveAttribute("href", "#history");
    expect(within(tabs).getByText("Events")).toHaveAttribute("aria-disabled", "true");
    expect(within(tabs).getByText("Incidents")).toHaveAttribute("title", "Incidents are not implemented yet");
  });

  it("states the outlook honestly: experimental, with no score", async () => {
    renderApp(view, { api: api(), route: "/hosts/web-1" });
    const id = await screen.findByLabelText("Host identity");
    expect(id).toHaveTextContent("no score");
    expect(id).toHaveTextContent("Risk Outlook is not available yet");
  });
});

describe("Host detail", () => {
  it("shows identity, health reasons and first/last seen", async () => {
    renderApp(view, { api: api(), route: "/hosts/web-1" });
    const id = await screen.findByLabelText("Host identity");
    expect(id).toHaveTextContent("checkout");
    expect(id).toHaveTextContent("agent-1");
    expect(id).toHaveTextContent("2026-11-01 00:00Z");
    expect(screen.getByLabelText("Health")).toHaveTextContent("memory is 88.0%, at or above 85%");
  });

  it("shows the backend-derived network rate, never the cumulative counter, and a dash when no rate exists", async () => {
    renderApp(view, { api: api(), route: "/hosts/web-1" });
    const cur = await screen.findByLabelText("Current values");
    await within(cur).findByText("2.0 MB/s");
    expect(cur).not.toHaveTextContent("9999999999");
    expect(cur).not.toHaveTextContent("10.0 GB/s");
    expect(cur).toHaveTextContent("rate unavailable");
  });

  it("marks missing and stale current metrics instead of showing zero", async () => {
    renderApp(view, { api: api(), route: "/hosts/web-1" });
    const cur = await screen.findByLabelText("Current values");
    await within(cur).findByText("12.5%");
    expect(cur).toHaveTextContent("— not reported"); // memory was not in the latest response
    expect(cur).toHaveTextContent("— stale");
    expect(cur).not.toHaveTextContent("0.0%");
    expect(cur).toHaveTextContent("Disk I/O and per-process metrics are not collected");
  });

  it("requests one series per metric for the selected range and breaks the line at gaps", async () => {
    const series = vi.fn(async (_id: string, p: { metric: string }) => seriesOf("web-1", p.metric, [1, 2, null, 4]));
    renderApp(view, { api: api({ series: series as never }), route: "/hosts/web-1?range=24h" });
    await screen.findByLabelText("CPU utilization history");
    await waitFor(() => expect(series).toHaveBeenCalledTimes(5));
    expect(series.mock.calls.map((c) => c[1].metric)).toContain("system.network.rx_bytes");
    const p = series.mock.calls[0]![1] as unknown as { start: string; end: string };
    expect(Date.parse(p.end) - Date.parse(p.start)).toBe(24 * 3_600_000);
    await waitFor(() => expect(document.querySelector("section[aria-label='CPU utilization history'] svg path")).not.toBeNull());
    const d = document.querySelector("section[aria-label='CPU utilization history'] svg path")!.getAttribute("d")!;
    expect(d.match(/M/g)).toHaveLength(2); // the null bucket splits the path in two
  });

  it("gives a metric-specific empty state when a series has no data", async () => {
    renderApp(view, { api: api({ series: (async (_id: string, p: { metric: string }) => seriesOf("web-1", p.metric, [null, null])) as never }), route: "/hosts/web-1" });
    const sec = await screen.findByLabelText("CPU utilization history");
    expect(await within(sec).findByText("No cpu utilization data")).toBeInTheDocument();
  });

  it("flags synthetic hosts as not real infrastructure", async () => {
    const s = host({ host_id: "sim0001-a", source: "synthetic", connected_infrastructure: false });
    renderApp(view, { api: api({ host: async () => detail(s), latest: async () => latestOf(s, []) }), route: "/hosts/sim0001-a" });
    expect(await screen.findByText(/Synthetic host: generated by the simulator/)).toBeInTheDocument();
  });

  it("shows a not-found error", async () => {
    renderApp(view, { api: api({ host: async () => { throw new ApiError(404, "host_not_found", "Host not found"); } }), route: "/hosts/nope" });
    expect(await screen.findByText("Not found")).toBeInTheDocument();
  });
});

// ----------------------------------------------------------------------------------- operational state, thresholds and alerts

const RULES = {
  "system.cpu.utilization": { warning: 80, critical: 90 },
  "system.memory.utilization": { warning: 85, critical: 95 },
  "system.disk.utilization": { warning: 90, critical: 95 },
};
const settingsDoc = (rules: typeof RULES = RULES) => ({
  config: { freshness: { stale_after_seconds: 90, offline_after_seconds: 300 }, rules },
  defaults: { freshness: { stale_after_seconds: 90, offline_after_seconds: 300 }, rules: RULES },
  version: 1, updated_at: null, updated_by: null,
});
const MIN = 60_000;
const at = (ms: number) => new Date(Date.now() - ms).toISOString();

const alertRow = (over: Partial<Alert> & { id: string }): Alert => ({
  host_id: "web-1", condition_key: "system.cpu.utilization|{}", rule: "threshold", metric: "system.cpu.utilization", labels: {},
  severity: "critical", state: "open", value: 97, threshold: 95, opened_at: at(10 * MIN), last_evaluated_at: at(MIN),
  acknowledged_at: null, acknowledged_by: null, resolved_at: null, resolved_by: null, resolution: null, ...over,
});

/** The alert list fake: records every request so tests can prove the host filter was sent. */
function alertsFake(rows: Alert[], opts: { total?: number; calls?: unknown[][]; fail?: unknown; pending?: boolean } = {}) {
  return async (status?: AlertStatusFilter, _s?: AbortSignal, limit?: number, hostId?: string): Promise<AlertList> => {
    opts.calls?.push([status, limit, hostId]);
    if (opts.pending) return new Promise<AlertList>(() => {});
    if (opts.fail) throw opts.fail;
    const scoped = rows.filter((r) => r.host_id === hostId);
    return { alerts: scoped.slice(0, limit ?? 100), total: opts.total ?? scoped.length };
  };
}

const latestFor = (samples: MetricSample[]) => async () => latestOf(h, samples);
const utilClasses = (root: HTMLElement) => Array.from(root.querySelectorAll(".util-bar i")).map((el) => el.className);

describe("Host detail: configured utilisation thresholds", () => {
  it("colours each metric by its configured limit, not by fixed cut-offs (82% CPU is warning under an 80% limit)", async () => {
    renderApp(view, {
      api: api({
        latest: latestFor([
          sample({ name: "system.cpu.utilization", value: 82 }),
          sample({ name: "system.memory.utilization", value: 90 }),
          sample({ name: "system.disk.utilization", labels: { mount: "C:" }, value: 96 }),
        ]),
        monitoringSettings: async () => settingsDoc(),
      }),
      route: "/hosts/web-1",
    });
    const cur = await screen.findByLabelText("Current values");
    await within(cur).findByText("82.0%");
    await waitFor(() => expect(utilClasses(cur)).toEqual(["warn", "warn", "crit"]));
    expect(cur).toHaveTextContent("warning ≥ 80% · critical ≥ 90%");
    expect(cur).toHaveTextContent("warning ≥ 85% · critical ≥ 95%");
    expect(cur).toHaveTextContent("warning ≥ 90% · critical ≥ 95%");
  });

  it("with thresholds unavailable, values carry no state colour and the page says why", async () => {
    renderApp(view, {
      api: api({
        latest: latestFor([sample({ name: "system.cpu.utilization", value: 99 })]),
        monitoringSettings: async () => { throw new ApiError(0, "network_error", "x"); },
      }),
      route: "/hosts/web-1",
    });
    const cur = await screen.findByLabelText("Current values");
    expect(await within(cur).findByText(/Thresholds could not be loaded/)).toBeInTheDocument();
    await within(cur).findByText("99.0%");
    expect(utilClasses(cur)).toEqual([""]);
    expect(cur).toHaveTextContent("thresholds unavailable");
  });

  it("a stale sample is labelled stale and never coloured, even when its value would breach", async () => {
    renderApp(view, {
      api: api({
        latest: latestFor([sample({ name: "system.cpu.utilization", value: 99, stale: true })]),
        monitoringSettings: async () => settingsDoc(),
      }),
      route: "/hosts/web-1",
    });
    const cur = await screen.findByLabelText("Current values");
    expect(await within(cur).findByText("— stale")).toBeInTheDocument();
    expect(cur).not.toHaveTextContent("99.0%");
    expect(utilClasses(cur)).toEqual([]);
  });
});

describe("Host detail: operational state", () => {
  it("keeps health and agent reporting as separate facts (healthy health while the agent is silent)", async () => {
    const silent = host({ host_id: "web-1", health: { status: "healthy", category: "healthy", reasons: [] }, reporting_status: "silent", last_seen_age_seconds: 4000 });
    renderApp(view, { api: api({ host: async () => detail(silent) }), route: "/hosts/web-1" });
    const strip = await screen.findByLabelText("Operational state");
    expect(strip).toHaveTextContent("Health");
    expect(within(strip).getByText("HEALTHY")).toBeInTheDocument();
    expect(within(strip).getByText("Offline")).toBeInTheDocument();
    expect(strip).toHaveTextContent("Last telemetry");
  });

  it("shows unknown health distinctly from a reporting agent", async () => {
    const unknown = host({ host_id: "web-1", health: { status: "unknown", category: "unknown", reasons: [] }, reporting_status: "reporting" });
    renderApp(view, { api: api({ host: async () => detail(unknown) }), route: "/hosts/web-1" });
    const strip = await screen.findByLabelText("Operational state");
    expect(within(strip).getByText("UNKNOWN")).toBeInTheDocument();
    expect(within(strip).getByText("Connected")).toBeInTheDocument();
  });

  it("states that the source label is derived from the host ID, not stored", async () => {
    renderApp(view, { api: api(), route: "/hosts/web-1" });
    expect(await screen.findByText(/derived from the host ID\. Stored telemetry provenance is not implemented yet/)).toBeInTheDocument();
  });
});

describe("Host detail: alerts for this host", () => {
  it("requests alerts filtered by this host on the server, never a fleet-wide list", async () => {
    const calls: unknown[][] = [];
    renderApp(view, { api: api({ alerts: alertsFake([], { calls }) }), route: "/hosts/web-1" });
    await screen.findByText("No alerts have been recorded for this host.");
    expect(calls.length).toBeGreaterThan(0);
    expect(calls.every((c) => c[2] === "web-1")).toBe(true);
  });

  it("separates active alerts (critical first) from recently resolved ones, labelling how each ended", async () => {
    const rows = [
      alertRow({ id: "warn", severity: "warning", opened_at: at(3 * MIN), metric: "system.memory.utilization", value: 88, threshold: 85 }),
      alertRow({ id: "crit", state: "acknowledged", acknowledged_at: at(5 * MIN), acknowledged_by: "user_1" }),
      alertRow({ id: "rec", host_id: "web-1", state: "resolved", resolved_at: at(2 * MIN), resolved_by: "system", resolution: "recovered", opened_at: at(30 * MIN) }),
      alertRow({ id: "man", host_id: "web-1", state: "resolved", resolved_at: at(4 * MIN), resolved_by: "user_2", resolution: "manual", opened_at: at(40 * MIN), metric: "system.disk.utilization", labels: { mount: "C:" } }),
    ];
    renderApp(view, { api: api({ alerts: alertsFake(rows) }), route: "/hosts/web-1" });
    const active = await screen.findByLabelText("Active alerts for this host");
    const rowsText = within(active).getAllByRole("row").slice(1).map((r) => r.textContent ?? "");
    expect(rowsText[0]).toContain("CPU utilisation");
    expect(rowsText[0]).toContain("Acknowledged by user_1");
    expect(rowsText[1]).toContain("Memory utilisation");
    expect(rowsText[1]).toContain("Not acknowledged");
    const resolved = screen.getByLabelText("Recently resolved alerts for this host");
    expect(resolved).toHaveTextContent("Recovered by telemetry");
    expect(resolved).toHaveTextContent("Resolved manually by user_2");
    expect(resolved).not.toHaveTextContent("Recovered by user_2");
    expect(resolved).toHaveTextContent("Disk utilisation (C:)");
  });

  it("links each alert to its own investigation, keeping the scope", async () => {
    renderApp(view, { api: api({ alerts: alertsFake([alertRow({ id: "abc-123" })]) }), route: "/hosts/web-1?source=live&range=24h" });
    const active = await screen.findByLabelText("Active alerts for this host");
    const link = within(active).getByRole("link", { name: "Investigate" });
    const url = new URL(link.getAttribute("href")!, "http://x");
    expect(url.pathname).toBe("/alerts/abc-123");
    expect(url.searchParams.get("source")).toBe("live");
    expect(url.searchParams.get("range")).toBe("24h");
  });

  it("acknowledges an open alert in place through the existing mutation", async () => {
    const ack = vi.fn(async (id: string) => alertRow({ id, state: "acknowledged", acknowledged_by: "user_test_1", acknowledged_at: at(MIN) }));
    renderApp(view, { api: api({ alerts: alertsFake([alertRow({ id: "open-1" })]), acknowledgeAlert: ack }), route: "/hosts/web-1" });
    fireEvent.click(await screen.findByRole("button", { name: /Acknowledge alert CPU utilisation/ }));
    await waitFor(() => expect(ack).toHaveBeenCalledWith("open-1"));
  });

  it("says when the host has alerts beyond the loaded page", async () => {
    renderApp(view, { api: api({ alerts: alertsFake([alertRow({ id: "one" })], { total: 250 }) }), route: "/hosts/web-1" });
    expect(await screen.findByText(/Showing the 1 most recent of 250 alerts for this host/)).toBeInTheDocument();
  });

  it("shows a loading state while the alert section loads", async () => {
    renderApp(view, { api: api({ alerts: alertsFake([], { pending: true }) }), route: "/hosts/web-1" });
    expect(await screen.findByText("Loading alerts for this host")).toBeInTheDocument();
  });

  it("shows an error, not an empty list, when the alerts request fails", async () => {
    renderApp(view, { api: api({ alerts: alertsFake([], { fail: new ApiError(0, "network_error", "x") }) }), route: "/hosts/web-1" });
    const sec = await screen.findByLabelText("Alerts for this host");
    expect(await within(sec).findByText("Could not reach the API")).toBeInTheDocument();
    expect(sec).not.toHaveTextContent("No alerts have been recorded");
  });

  it("shows an empty state when the host has no alerts, and says so when none are active", async () => {
    renderApp(view, { api: api({ alerts: alertsFake([]) }), route: "/hosts/web-1" });
    expect(await screen.findByText("No alerts have been recorded for this host.")).toBeInTheDocument();
  });

  it("says there are no active alerts when only resolved ones exist", async () => {
    const rows = [alertRow({ id: "r", state: "resolved", resolved_at: at(MIN), resolved_by: "system", resolution: "recovered" })];
    renderApp(view, { api: api({ alerts: alertsFake(rows) }), route: "/hosts/web-1" });
    expect(await screen.findByText("No active alerts for this host.")).toBeInTheDocument();
    expect(screen.getByLabelText("Recently resolved alerts for this host")).toBeInTheDocument();
  });
});

describe("Host detail: never reported", () => {
  it("a registered host with no telemetry yet is explained, not reported as not found", async () => {
    renderApp(view, {
      api: api({
        host: async () => { throw new ApiError(404, "host_not_found", "Host not found"); },
        registryHosts: async () => [{
          host_id: "web-1", kind: "live", display_name: null, created_at: "2026-11-18T10:00:00Z", created_by: "user_1",
          deactivated_at: null, telemetry_state: "awaiting_first_telemetry", last_envelope_at: null, active_credentials: 1,
        }],
      }),
      route: "/hosts/web-1",
    });
    expect(await screen.findByText("Registered, no telemetry yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open Agents/ })).toHaveAttribute("href", "/settings/agents");
  });

  it("a host unknown to both the host API and the registry is simply not found", async () => {
    renderApp(view, {
      api: api({ host: async () => { throw new ApiError(404, "host_not_found", "Host not found"); }, registryHosts: async () => [] }),
      route: "/hosts/nope",
    });
    expect(await screen.findByText("Not found")).toBeInTheDocument();
  });
});

describe("Host detail: returning to the filtered list", () => {
  it("All hosts returns to the list the user came from, with its filters", async () => {
    renderApp(view, { api: api(), route: "/hosts/web-1?from=%2Fhosts%3Fhealth%3Dcritical%26cluster%3Dcl-a" });
    const back = await screen.findAllByRole("link", { name: "All hosts" });
    expect(back[0]).toHaveAttribute("href", "/hosts?health=critical&cluster=cl-a");
  });

  it("ignores a return path that does not point into the Hosts list", async () => {
    renderApp(view, { api: api(), route: "/hosts/web-1?from=https%3A%2F%2Fexample.com" });
    const back = await screen.findAllByRole("link", { name: "All hosts" });
    expect(back[0]).toHaveAttribute("href", "/hosts");
  });
});

describe("Host detail: identity, registry placement and history limits", () => {
  it("shows health, source and the registry placement from the host record", async () => {
    const placed = { ...h, environment: { id: "env-prod", name: "prod" }, region: null, cluster: { id: "cl-a", name: "alpha" }, services: [{ id: "svc-api", name: "api" }] };
    renderApp(view, { api: api({ host: async () => detail(placed) }), route: "/hosts/web-1" });
    const bar = await screen.findByLabelText("Host summary");
    expect(within(bar).getByText("WARNING")).toBeInTheDocument();
    expect(within(bar).getByText("LIVE")).toBeInTheDocument();
    const identity = screen.getByLabelText("Host identity");
    expect(within(identity).getByRole("link", { name: "prod" })).toHaveAttribute("href", "/infrastructure");
    expect(within(identity).getByRole("link", { name: "alpha" })).toHaveAttribute("href", "/infrastructure");
    expect(within(identity).getByRole("link", { name: "api" })).toHaveAttribute("href", "/infrastructure");
  });

  it("states that disk I/O and process count are not collected, rather than drawing empty charts", async () => {
    renderApp(view, { api: api(), route: "/hosts/web-1" });
    const disk = await screen.findByLabelText("Disk I/O history");
    expect(within(disk).getByText(/does not collect disk i\/o/)).toBeInTheDocument();
    expect(screen.getByLabelText("Process count history")).toHaveTextContent("not collected");
  });

  it("buckets the host's loaded alerts over the selected range and says when the list is capped", async () => {
    const recent = alertRow({ id: "a-recent", opened_at: new Date(Date.now() - 30 * 60_000).toISOString() });
    renderApp(view, {
      api: api({ alerts: async () => ({ alerts: [recent], total: 500 }) }),
      route: "/hosts/web-1?range=6h",
    });
    const timeline = await screen.findByLabelText("Alerts opened for this host in the last 6h");
    expect(within(timeline).getByText(/1 opened in the last 6h/)).toBeInTheDocument();
    expect(within(timeline).getByText(/capped at the most recent alerts/)).toBeInTheDocument();
  });
});
