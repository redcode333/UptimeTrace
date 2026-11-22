import { fireEvent, screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Alert, Host, HostList } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { host, hostList } from "../../test/fixtures";
import { OverviewPage } from "./OverviewPage";
import { histogramFake, rankingFake } from "../../test/commandCenterFakes";

const none = { disk_percent_max: null, disk_mount_max: null, network_rx_bytes_per_second: null, network_tx_bytes_per_second: null };
const at = (minutesAgo: number) => new Date(Date.now() - minutesAgo * 60_000).toISOString();
const withCpu = (id: string, hostname: string, cpu: number | null, over: Partial<Host> = {}) =>
  host({ host_id: id, hostname, latest: { cpu_percent: cpu, memory_percent: 40, ...none }, ...over } as never);

const alertOn = (id: string, hostId: string, minutesAgo: number, severity: "critical" | "warning" = "critical"): Alert => ({
  id, host_id: hostId, condition_key: id, rule: "threshold", metric: "system.cpu.utilization", labels: {}, severity, state: "open",
  value: 97, threshold: 95, opened_at: at(minutesAgo), last_evaluated_at: at(0), acknowledged_at: null, acknowledged_by: null,
  resolved_at: null, resolved_by: null, resolution: null,
});

function api(opts: {
  items?: Host[]; total?: number; alerts?: Alert[]; alertsTotal?: number; hostsFail?: boolean; alertsFail?: boolean; alertsPending?: boolean;
  histFail?: boolean; histPending?: boolean; histTotal?: { critical: number; warning: number };
} = {}) {
  const items = opts.items ?? [withCpu("web-1", "web-1", 97), withCpu("db-1", "db-1", 35), withCpu("cache-1", "cache-1", null)];
  return fakeApi({
    hosts: async (p = {}) => {
      // the fleet request asks for 200 rows; the summary asks for 1. Failing only the fleet keeps the page's counts available.
      if (opts.hostsFail && (p.page_size ?? 0) > 1) throw new Error("hosts down");
      return hostList(items, {
        total: opts.total ?? items.length,
        facets: { source: { live: items.length, synthetic: 0, replay: 0 }, health: { healthy: items.length, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0 } },
      }) as HostList;
    },
    rankings: async (p) => rankingFake(items, p.metric, p.limit, p.source),
    alertHistogram: async (p) => {
      if (opts.histFail) throw new Error("histogram down");
      if (opts.histPending) return new Promise<never>(() => {});
      return histogramFake(opts.alerts ?? [], p, opts.histTotal);
    },
    alerts: async () => {
      if (opts.alertsFail) throw new Error("alerts down");
      if (opts.alertsPending) return new Promise<never>(() => {});
      const alerts = opts.alerts ?? [];
      return { alerts, total: opts.alertsTotal ?? alerts.length };
    },
  });
}

const routes = (
  <Routes>
    <Route path="/" element={<OverviewPage />} />
    <Route path="/hosts/:hostId" element={<div>HOST DETAIL PAGE</div>} />
    <Route path="/hosts" element={<div>HOSTS PAGE</div>} />
  </Routes>
);

describe("Command Center: host map selection", () => {
  it("opens the selected host's detail page when its hexagon is clicked", async () => {
    renderApp(routes, { api: api(), route: "/" });
    await screen.findByRole("img", { name: /Host map: hosts 1–3 of 3 in scope/ });
    const cell = document.querySelector('a[aria-label^="web-1 · LIVE"]') as HTMLElement;
    expect(cell).not.toBeNull();
    fireEvent.click(cell);
    expect(await screen.findByText("HOST DETAIL PAGE")).toBeInTheDocument();
  });

  it("says why a host with no CPU value is shown flat, using its reporting state", async () => {
    renderApp(routes, { api: api({ items: [withCpu("cache-1", "cache-1", null, { reporting_status: "stale" } as never)] }), route: "/" });
    await screen.findByRole("img", { name: /Host map: hosts 1–1 of 1 in scope/ });
    expect(document.querySelector('a[aria-label*="no current CPU: stale or silent: no fresh value"]')).not.toBeNull();
  });

  it("states when the map covers only part of the scope, using the real total", async () => {
    renderApp(routes, { api: api({ total: 250 }), route: "/" });
    expect(await screen.findByText(/Showing hosts 1–3 of 250 in scope; 247 more are not loaded here/)).toBeInTheDocument();
  });

  it("shows the map as unavailable, not as an empty fleet, when the host request fails", async () => {
    renderApp(routes, { api: api({ hostsFail: true }), route: "/" });
    expect(await screen.findByText(/Host map unavailable: the request failed/)).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Host map: hosts/ })).toBeNull();
    // the counts from the summary request are still shown
    expect(screen.getByText("Total hosts")).toBeInTheDocument();
  });
});

describe("Command Center: rankings", () => {
  it("counts hosts without a current value instead of ranking them as zero", async () => {
    renderApp(routes, { api: api(), route: "/" });
    expect(await screen.findByText(/1 in scope has no current value and is not ranked/)).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "CPU now by host" });
    expect(within(list).queryByText("cache-1")).toBeNull();
  });

  it("shows loading, not an empty ranking, while the fleet is still arriving", async () => {
    renderApp(routes, { api: fakeApi({ hosts: () => new Promise<never>(() => {}) }), route: "/" });
    expect(await screen.findByText("Loading fleet")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "CPU now by host" })).toBeNull();
  });
});

describe("Command Center: alert histogram", () => {
  it("draws alerts opened in the selected time range, with each bar's real time and counts in its tooltip", async () => {
    renderApp(routes, { api: api({ alerts: [alertOn("a1", "web-1", 5), alertOn("a2", "db-1", 40, "warning")] }), route: "/?range=1h" });
    const chart = await screen.findByRole("img", { name: /2 alerts opened in the last 1h/ });
    const titles = Array.from(chart.querySelectorAll("title")).map((t) => t.textContent ?? "");
    expect(titles.some((t) => /1 critical/.test(t) && /UTC/.test(t))).toBe(true);
    expect(titles.some((t) => /1 warning/.test(t))).toBe(true);
  });

  it("changes the window with the top-bar time range", async () => {
    renderApp(routes, { api: api({ alerts: [alertOn("a1", "web-1", 5)] }), route: "/?range=24h" });
    expect(await screen.findByRole("img", { name: /alerts opened in the last 24h/ })).toBeInTheDocument();
  });

  it("does not claim zero alerts when the alert request failed", async () => {
    renderApp(routes, { api: api({ histFail: true }), route: "/" });
    expect(await screen.findByText(/Alert history unavailable: the request failed/)).toBeInTheDocument();
    expect(screen.queryByText(/No alert opened/)).toBeNull();
  });

  it("shows loading, not zero, while alerts are pending", async () => {
    renderApp(routes, { api: api({ histPending: true }), route: "/" });
    expect(await screen.findByText(/Loading alert history…/)).toBeInTheDocument();
  });

  it("counts every alert in the window from the server, not only the newest loaded", async () => {
    renderApp(routes, { api: api({ alerts: [alertOn("a1", "web-1", 5)], histTotal: { critical: 340, warning: 0 } }), route: "/" });
    expect(await screen.findByText(/340 opened/)).toBeInTheDocument();
  });
});

describe("Command Center: insights", () => {
  it("lists an open critical alert with its real host, and states the outlook is not available", async () => {
    renderApp(routes, { api: api({ alerts: [alertOn("a1", "web-1", 10)] }), route: "/" });
    const insights = await screen.findByRole("list", { name: "Insights" });
    expect(within(insights).getByText(/web-1 is past its configured limit/)).toBeInTheDocument();
    expect(within(insights).getByText(/Outlook · not available/)).toBeInTheDocument();
  });
});
