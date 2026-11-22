import { fireEvent, screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Alert, Host, HostList } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { host, hostList } from "../../test/fixtures";
import { OverviewPage } from "./OverviewPage";
import { histogramFake, rankingFake } from "../../test/commandCenterFakes";

const none = { disk_percent_max: null, disk_mount_max: null, network_rx_bytes_per_second: null, network_tx_bytes_per_second: null };
const withCpu = (id: string, hostname: string, cpu: number | null, over: Partial<Host> = {}) =>
  host({ host_id: id, hostname, latest: { cpu_percent: cpu, memory_percent: 40, ...none }, ...over } as never);

const fleet: Host[] = [
  withCpu("web-1", "web-1", 97, { health: { status: "critical", category: "degraded", reasons: [] } } as never),
  withCpu("db-1", "db-1", 35),
  withCpu("cache-1", "cache-1", null),
];

function fleetApi(items: Host[] = fleet, alerts: Alert[] = []) {
  return fakeApi({
    hosts: async () => hostList(items, {
      facets: { source: { live: items.length, synthetic: 0, replay: 0 }, health: { healthy: 1, warning: 0, critical: 1, stale: 0, offline: 0, unknown: 1 } },
    }) as HostList,
    alerts: async () => ({ alerts, total: alerts.length }),
    rankings: async (p) => rankingFake(items, p.metric, p.limit, p.source),
    alertHistogram: async (p) => histogramFake(alerts, p),
  });
}

const page = (
  <Routes>
    <Route path="/" element={<OverviewPage />} />
    <Route path="/hosts" element={<div>HOSTS PAGE</div>} />
  </Routes>
);

describe("Command Center panels", () => {
  it("draws the host map in 3D by default and labels what the height means", async () => {
    renderApp(page, { api: fleetApi() });
    const map = await screen.findByRole("img", { name: /Host map: hosts 1–3 of 3 in scope/ });
    expect(map).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /height = CPU now/ })).toBeInTheDocument();
  });

  it("switches the host map to 2D without losing the hosts", async () => {
    renderApp(page, { api: fleetApi() });
    const map = await screen.findByLabelText("Map view");
    fireEvent.click(within(map).getByRole("button", { name: "2D" }));
    expect(within(map).getByRole("button", { name: "2D" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { name: /Host map · colour = confirmed state$/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Host map: hosts 1–3 of 3 in scope/ })).toBeInTheDocument();
  });

  it("lists CPU only for hosts that reported it, highest first, and never ranks a missing value as zero", async () => {
    renderApp(page, { api: fleetApi() });
    const list = await screen.findByRole("list", { name: "CPU now by host" });
    const names = within(list).getAllByRole("link").map((a) => a.textContent);
    expect(names).toEqual(["web-1", "db-1"]);
    expect(within(list).queryByText("cache-1")).toBeNull();
  });

  it("links each host in the CPU and memory rankings to its detail page", async () => {
    renderApp(page, { api: fleetApi() });
    const list = await screen.findByRole("list", { name: "CPU now by host" });
    expect(within(list).getByRole("link", { name: "web-1" })).toHaveAttribute("href", expect.stringMatching(/^\/hosts\/web-1/));
  });

  it("shows an open critical alert as a rule-fired insight with the host's real name", async () => {
    const alert: Alert = {
      id: "a1", host_id: "web-1", condition_key: "k", rule: "threshold", metric: "system.cpu.utilization", labels: {}, severity: "critical",
      state: "open", value: 97, threshold: 95, opened_at: new Date(Date.now() - 600_000).toISOString(), last_evaluated_at: new Date().toISOString(),
      acknowledged_at: null, acknowledged_by: null, resolved_at: null, resolved_by: null, resolution: null,
    };
    renderApp(page, { api: fleetApi(fleet, [alert]) });
    const insights = await screen.findByRole("list", { name: "Insights" });
    expect(within(insights).getByText("Rule fired · CPU utilization")).toBeInTheDocument();
    expect(within(insights).getByText(/web-1 is past its configured limit/)).toBeInTheDocument();
  });

  it("says the outlook is not available and shows no forecast number", async () => {
    renderApp(page, { api: fleetApi() });
    const insights = await screen.findByRole("list", { name: "Insights" });
    expect(within(insights).getByText(/Outlook · not available/)).toBeInTheDocument();
    expect(insights.textContent).not.toMatch(/risk \d/i);
  });

  it("states that no alert opened when none exists, instead of drawing an empty histogram", async () => {
    renderApp(page, { api: fleetApi() });
    expect(await screen.findByText("No alert opened in the last 6h.")).toBeInTheDocument();
  });

  it("keeps LIVE tiles on the scope-preserving host list link", async () => {
    renderApp(page, { api: fleetApi() });
    const live = await screen.findByLabelText("LIVE only");
    expect(within(live).getByText("Connected hosts").closest("a")).toHaveAttribute("href", expect.stringMatching(/^\/hosts/));
  });
});
