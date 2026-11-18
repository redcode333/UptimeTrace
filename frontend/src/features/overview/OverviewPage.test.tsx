import { screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { HostListParams } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { host, hostList } from "../../test/fixtures";
import { OverviewPage, distribution } from "./OverviewPage";

const none = { disk_percent_max: null, disk_mount_max: null, network_rx_bytes_per_second: null, network_tx_bytes_per_second: null };
const web = host({
  host_id: "web-1",
  latest: { cpu_percent: 97, memory_percent: 50, ...none },
  health: { status: "critical", reasons: [{ metric: "system.cpu.utilization", labels: {}, value: 97, threshold: 95, level: "critical" }] },
});
const db = host({ host_id: "db-1" });
const off = host({ host_id: "sim0001-x", source: "synthetic", connected_infrastructure: false, last_seen_age_seconds: 4000, health: { status: "offline", reasons: [] } });

function api(calls: HostListParams[] = []) {
  return fakeApi({
    hosts: async (p = {}) => {
      calls.push(p);
      const items = p.health ? [off, web] : [web, db, off];
      return hostList(items, {
        facets: { source: { live: 2, synthetic: 1, replay: 0 }, health: { healthy: 1, warning: 0, critical: 1, offline: 1, unknown: 0 } },
      });
    },
  });
}
const page = (
  <Routes>
    <Route path="/" element={<OverviewPage />} />
    <Route path="/hosts" element={<div>HOSTS PAGE</div>} />
  </Routes>
);

describe("Overview", () => {
  it("shows host counts and the source breakdown from the hosts API", async () => {
    renderApp(page, { api: api() });
    const kpis = await screen.findByLabelText("Host health");
    expect(within(kpis).getByText("Critical").nextElementSibling).toHaveTextContent("1");
    expect(within(kpis).getByText("Total hosts").nextElementSibling).toHaveTextContent("3");
    const sources = screen.getByLabelText("Data sources");
    expect(within(sources).getByText("SYNTHETIC")).toBeInTheDocument();
    expect(within(sources).getAllByText("in scope")).toHaveLength(3);
  });

  it("applies the global source scope to every request and labels excluded sources", async () => {
    const calls: HostListParams[] = [];
    renderApp(page, { api: api(calls), route: "/?source=live" });
    await screen.findByLabelText("Host health");
    expect(calls.length).toBeGreaterThanOrEqual(2);
    expect(calls.every((c) => c.source?.join() === "live")).toBe(true);
    expect(screen.getAllByText("excluded by scope")).toHaveLength(2);
  });

  it("lists unhealthy hosts worst-first with the reason, linking to the host", async () => {
    renderApp(page, { api: api() });
    const table = await screen.findByLabelText("Hosts needing attention");
    const rows = await within(table).findAllByRole("row");
    expect(rows[1]).toHaveTextContent("web-1");
    expect(rows[1]).toHaveTextContent("cpu 97.0% ≥ 95%");
    expect(rows[2]).toHaveTextContent("sim0001-x");
    expect(within(table).getByRole("link", { name: "web-1" })).toHaveAttribute("href", "/hosts/web-1");
  });

  it("makes each KPI a link to the Hosts view filtered by that health, keeping scope and range", async () => {
    renderApp(page, { api: api(), route: "/?source=live,replay&range=24h" });
    const kpis = await screen.findByLabelText("Host health");
    const href = within(kpis).getByText("Critical").closest("a")!.getAttribute("href")!;
    const url = new URL(href, "http://x");
    expect(url.pathname).toBe("/hosts");
    expect(url.searchParams.get("health")).toBe("critical");
    expect(url.searchParams.get("source")).toBe("live,replay");
    expect(url.searchParams.get("range")).toBe("24h");
  });

  it("counts hosts with no measurement as no-data, never as 0%", () => {
    const d = distribution([web, db, off], (h) => h.latest.disk_percent_max);
    expect(d.noData).toBe(1); // web has a null disk
    expect(d.buckets.reduce((a, b) => a + b, 0)).toBe(2);
  });

  it("shows an honest empty state", async () => {
    renderApp(page, { api: fakeApi({ hosts: async () => hostList([], { total: 0 }) }) });
    expect(await screen.findByText("No hosts have reported yet")).toBeInTheDocument();
  });

  it("shows an error state with retry when the API is unreachable", async () => {
    const failing = vi.fn(async () => { throw new ApiError(0, "network_error", "x"); });
    renderApp(page, { api: fakeApi({ hosts: failing }) });
    expect(await screen.findByText("Could not reach the API")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument());
  });

  it("does not invent an activity feed", async () => {
    renderApp(page, { api: api() });
    expect(await screen.findByText(/No event or incident feed exists/)).toBeInTheDocument();
  });
});
