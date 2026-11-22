import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { Alert, AlertList, Host, HostListParams } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { host, hostList } from "../../test/fixtures";
import { OverviewPage, distribution } from "./OverviewPage";
import { attentionFakeOrder, distributionFake } from "../../test/commandCenterFakes";

const none = { disk_percent_max: null, disk_mount_max: null, network_rx_bytes_per_second: null, network_tx_bytes_per_second: null };
const web = host({
  host_id: "web-1",
  latest: { cpu_percent: 97, memory_percent: 50, ...none },
  health: { status: "critical", category: "degraded", reasons: [{ metric: "system.cpu.utilization", labels: {}, value: 97, threshold: 95, level: "critical", key: "system.cpu.utilization|{}" }] },
});
const db = host({ host_id: "db-1" });
const off = host({ host_id: "sim0001-x", source: "synthetic", connected_infrastructure: false, last_seen_age_seconds: 4000, health: { status: "offline", category: "offline", reasons: [] } });

function api(calls: HostListParams[] = []) {
  return fakeApi({
    hosts: async (p = {}) => {
      calls.push(p);
      const items = p.health ? (p.sort === "attention" ? [web, off] : [off, web]) : [web, db, off];
      return hostList(items, {
        facets: { source: { live: 2, synthetic: 1, replay: 0 }, health: { healthy: 1, warning: 0, critical: 1, stale: 0, offline: 1, unknown: 0 } },
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
    // Every scoped request is scoped to the selection. The one unscoped request is the all-sources host list used only to
    // recognise which alerts belong to LIVE hosts (docs/FRONTEND_ARCHITECTURE.md section 6.4); it is never shown as a count.
    expect(calls.filter((c) => c.source).every((c) => c.source?.join() === "live")).toBe(true);
    expect(calls.filter((c) => !c.source)).toHaveLength(1);
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
    // Each panel reports its own failure and offers a retry; none of them blanks the page.
    const errors = await screen.findAllByText("Could not reach the API");
    expect(errors.length).toBeGreaterThan(1);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Retry" }).length).toBeGreaterThan(1));
  });

  it("describes recent activity as alert transitions only, never as a general event feed", async () => {
    renderApp(page, { api: api() });
    expect(await screen.findByText(/Alert transitions only\. Severity changes, agent starts and stops, and host state changes are not recorded here\./)).toBeInTheDocument();
    expect(screen.queryByText(/No event or incident feed exists/)).not.toBeInTheDocument();
  });
});

// ------------------------------------------------------------------------------------------- Command Center panels (CC-1 to CC-7)

const NOW = Date.now();
const MIN = 60_000;
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const RULES = {
  "system.cpu.utilization": { warning: 85, critical: 95 },
  "system.memory.utilization": { warning: 85, critical: 95 },
  "system.disk.utilization": { warning: 90, critical: 95 },
};
const cpuReason = (v: number) => [{ metric: "system.cpu.utilization", labels: {}, value: v, threshold: 95, level: "critical" as const, key: "system.cpu.utilization|{}" }];

const fleetHosts = {
  web: host({ host_id: "web-1", latest: { cpu_percent: 97, memory_percent: 40, ...none }, health: { status: "critical", category: "degraded", reasons: cpuReason(97) } }),
  edge: host({ host_id: "edge-1", latest: { cpu_percent: 95, memory_percent: 40, ...none }, health: { status: "critical", category: "degraded", reasons: cpuReason(95) } }),
  mid: host({ host_id: "mid-1", latest: { cpu_percent: 90, memory_percent: 40, ...none }, health: { status: "warning", category: "degraded", reasons: [{ metric: "system.cpu.utilization", labels: {}, value: 90, threshold: 85, level: "warning", key: "system.cpu.utilization|{}" }] } }),
  low: host({ host_id: "low-1", latest: { cpu_percent: 50, memory_percent: 40, ...none } }),
  blank: host({ host_id: "blank-1", latest: { cpu_percent: null, memory_percent: null, ...none }, health: { status: "unknown", category: "unknown", reasons: [] } }),
  gone: host({ host_id: "gone-1", latest: { cpu_percent: null, memory_percent: null, ...none }, last_seen_age_seconds: 4000, reporting_status: "silent", health: { status: "offline", category: "offline", reasons: [] } }),
  sim: host({ host_id: "sim0001-x", source: "synthetic", connected_infrastructure: false, last_seen_age_seconds: 4000, health: { status: "offline", category: "offline", reasons: [] } }),
};

function manyHosts(n: number, status: "healthy" | "warning" = "healthy", cpu = 10): Host[] {
  return Array.from({ length: n }, (_, i) =>
    host({
      host_id: `bulk-${String(i).padStart(3, "0")}`,
      latest: { cpu_percent: cpu, memory_percent: 10, ...none },
      health: status === "healthy"
        ? { status: "healthy", category: "healthy", reasons: [] }
        : { status: "warning", category: "degraded", reasons: [{ metric: "system.cpu.utilization", labels: {}, value: cpu, threshold: 85, level: "warning", key: "system.cpu.utilization|{}" }] },
    }));
}

const alertRow = (over: Partial<Alert> & { id: string; host_id: string }): Alert => ({
  condition_key: "system.cpu.utilization|{}", rule: "threshold", metric: "system.cpu.utilization", labels: {}, severity: "critical",
  state: "open", value: 97, threshold: 95, opened_at: ago(10 * MIN), last_evaluated_at: ago(MIN), acknowledged_at: null,
  acknowledged_by: null, resolved_at: null, resolved_by: null, resolution: null, ...over,
});

interface CcOptions {
  hosts?: Host[];
  alerts?: Alert[];
  alertTotal?: number;
  failAlerts?: unknown;
  failHosts?: unknown;
  hostCalls?: HostListParams[];
  alertCalls?: { status?: string; limit?: number }[];
  ack?: (id: string) => Promise<Alert>;
}

/** A fake API that behaves like the real one: scope filters the items, and facets follow the backend's rules. */
function ccApi(o: CcOptions = {}) {
  const all = o.hosts ?? Object.values(fleetHosts);
  return fakeApi({
    hosts: async (p: HostListParams = {}) => {
      o.hostCalls?.push(p);
      if (o.failHosts) throw o.failHosts;
      let items = all;
      if (p.source) items = items.filter((h) => p.source!.includes(h.source));
      const source = { live: all.filter((h) => h.source === "live").length, synthetic: all.filter((h) => h.source === "synthetic").length, replay: 0 };
      const healthPool = p.source ? all.filter((h) => p.source!.includes(h.source)) : all;
      const health = { healthy: 0, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0 };
      for (const h of healthPool) health[h.health.status]++;
      if (p.health) items = items.filter((h) => p.health!.includes(h.health.status));
      if (p.sort === "attention") items = attentionFakeOrder(items);
      const pageSize = p.page_size ?? 50;
      return hostList(items.slice(0, pageSize), {
        total: items.length, page_size: pageSize, facets: { source, health },
        thresholds: { host_stale_after_seconds: 90, offline_after_seconds: 300, stale_after_seconds: 300, rules: RULES },
      });
    },
    distribution: async (p) => distributionFake(all, p.metric, RULES, p.source),
    alerts: async (status = "active", _signal?: AbortSignal, limit?: number): Promise<AlertList> => {
      o.alertCalls?.push({ status, limit });
      if (o.failAlerts) throw o.failAlerts;
      const pool = o.alerts ?? [];
      const rows = status === "active" ? pool.filter((a) => a.state !== "resolved") : status === "resolved" ? pool.filter((a) => a.state === "resolved") : pool;
      const total = o.alertTotal ?? rows.length;
      return { alerts: rows.slice(0, limit ?? 100), total };
    },
    acknowledgeAlert: o.ack ?? (async (id: string) => alertRow({ id, host_id: "web-1", state: "acknowledged", acknowledged_by: "user_test_1", acknowledged_at: ago(MIN) })),
  });
}

const ccPage = (
  <Routes>
    <Route path="/" element={<OverviewPage />} />
    <Route path="/hosts" element={<div>HOSTS PAGE</div>} />
    <Route path="/alerts" element={<div>ALERTS PAGE</div>} />
  </Routes>
);

describe("Command Center: utilisation and thresholds (CC-7, CC-4)", () => {
  it("colours and tallies against the configured limits; a host at exactly the critical limit is critical", async () => {
    renderApp(ccPage, { api: ccApi() });
    // CPU across the fleet: 97 and 95 critical, 90 warning, 50 and 10 normal, two with no value (blank and silent).
    expect((await screen.findAllByText(/Warning 1 · Critical 2 · Normal 2/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/2 with no current value/).length).toBeGreaterThan(0);
  });

  it("never shows a host with no measurement as 0%", async () => {
    renderApp(ccPage, { api: ccApi() });
    const table = await screen.findByLabelText("Hosts needing attention");
    const rows = await within(table).findAllByRole("row");
    const blank = rows.find((r) => r.textContent?.includes("blank-1"));
    expect(blank).toBeDefined();
    expect(blank!.textContent).toContain("No fresh CPU, memory or disk measurement");
    expect(blank!.textContent).not.toMatch(/0\.0%/);
  });

  it("orders the whole fleet by severity on the server, so a critical host beyond the first 100 still leads the table", async () => {
    const hosts = [...manyHosts(101, "warning"), fleetHosts.web];
    renderApp(ccPage, { api: ccApi({ hosts }) });
    const table = await screen.findByLabelText("Hosts needing attention");
    expect(await within(table).findByText("Top 10 of 102 not healthy")).toBeInTheDocument();
    const rows = await within(table).findAllByRole("row");
    expect(rows[1]).toHaveTextContent("web-1");
    expect(within(table).queryByText(/Ranked among/)).toBeNull();
  });

  it("counts the distributions over the whole in-scope fleet, not only the first 200 hosts (201 hosts)", async () => {
    renderApp(ccPage, { api: ccApi({ hosts: manyHosts(201) }) });
    const panel = await screen.findByLabelText("Utilisation distribution");
    expect(await within(panel).findByText("201 hosts in scope")).toBeInTheDocument();
    expect(within(panel).queryByText(/First 200 hosts by name/)).toBeNull();
  });

  it("does not caption a distribution as partial at exactly 200 hosts", async () => {
    renderApp(ccPage, { api: ccApi({ hosts: manyHosts(200) }) });
    const panel = await screen.findByLabelText("Utilisation distribution");
    expect(within(panel).queryByText(/First 200 hosts by name/)).not.toBeInTheDocument();
  });
});

describe("Command Center: active alerts (CC-5)", () => {
  it("shows exact counts when the alert list is complete, and acknowledging refreshes the alert query", async () => {
    const alerts = [alertRow({ id: "a1", host_id: "web-1" }), alertRow({ id: "a2", host_id: "mid-1", severity: "warning", value: 90, threshold: 85 })];
    const ack = vi.fn(async (id: string) => alertRow({ id, host_id: "web-1", state: "acknowledged", acknowledged_by: "user_test_1", acknowledged_at: ago(MIN) }));
    const alertCalls: { status?: string; limit?: number }[] = [];
    renderApp(ccPage, { api: ccApi({ alerts, ack, alertCalls }) });
    const counts = await screen.findByLabelText("Alert counts");
    expect(counts).toHaveTextContent("In scope: 2");
    expect(counts).toHaveTextContent("Critical 1");
    expect(counts).toHaveTextContent("Warning 1");
    const before = alertCalls.filter((c) => c.status === "active").length;
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge alert for web-1" }));
    await waitFor(() => expect(ack).toHaveBeenCalledWith("a1"));
    await waitFor(() => expect(alertCalls.filter((c) => c.status === "active").length).toBeGreaterThan(before));
  });

  it("requests up to 200 active alerts, not the default 100", async () => {
    const alertCalls: { status?: string; limit?: number }[] = [];
    renderApp(ccPage, { api: ccApi({ alerts: [alertRow({ id: "a1", host_id: "web-1" })], alertCalls }) });
    await screen.findByLabelText("Alert counts");
    expect(alertCalls.some((c) => c.status === "active" && c.limit === 200)).toBe(true);
  });

  it("a capped alert list is partial and is never presented as the total", async () => {
    const alerts = Array.from({ length: 200 }, (_, i) => alertRow({ id: `cap-${i}`, host_id: "web-1", opened_at: ago((i + 1) * MIN) }));
    renderApp(ccPage, { api: ccApi({ alerts, alertTotal: 250 }) });
    expect(await screen.findByText(/Partial: 200 of 250 active alerts loaded/)).toBeInTheDocument();
    expect(screen.getByLabelText("Alert counts")).toHaveTextContent("Not exact for this scope");
    expect(screen.getByLabelText("Alert counts")).toHaveTextContent("Critical (loaded) 200");
    expect(screen.getByLabelText("Active alerts")).toHaveTextContent("250 active");
  });

  it("with a subset selected, an alert on an out-of-scope host is not counted in scope", async () => {
    const alerts = [
      alertRow({ id: "a1", host_id: "web-1" }),
      alertRow({ id: "a2", host_id: "sim0001-x", rule: "host_offline", metric: null, value: null, threshold: null }),
    ];
    renderApp(ccPage, { api: ccApi({ alerts }), route: "/?source=live" });
    const counts = await screen.findByLabelText("Alert counts");
    expect(counts).toHaveTextContent("In scope: 1");
    expect(counts).toHaveTextContent("Host offline 0");
  });

  it("an alert whose host is not loaded is reported as unknown-source, not dropped or assigned", async () => {
    const alerts = [alertRow({ id: "a1", host_id: "web-1" }), alertRow({ id: "a9", host_id: "ghost-9" })];
    renderApp(ccPage, { api: ccApi({ alerts }), route: "/?source=live" });
    expect(await screen.findByText(/belong to hosts not in the loaded host list/)).toBeInTheDocument();
    expect(screen.getByLabelText("Alert counts")).toHaveTextContent("Not exact for this scope");
  });

  it("a failed alert request shows its own error and leaves the host panels rendered", async () => {
    renderApp(ccPage, { api: ccApi({ failAlerts: new ApiError(0, "network_error", "x") }) });
    const panel = await screen.findByLabelText("Active alerts");
    expect(await within(panel).findByText("Could not reach the API")).toBeInTheDocument();
    const attention = await screen.findByLabelText("Hosts needing attention");
    expect(within(attention).getByRole("link", { name: "web-1" })).toBeInTheDocument();
    expect(screen.getByLabelText("Host health")).toBeInTheDocument();
  });
});

describe("Command Center: recent alert transitions (CC-6)", () => {
  it("labels a system recovery and a manual resolution differently, with their actors", async () => {
    const alerts = [
      alertRow({ id: "rec", host_id: "web-1", state: "resolved", opened_at: ago(20 * MIN), resolved_at: ago(5 * MIN), resolved_by: "system", resolution: "recovered" }),
      alertRow({ id: "man", host_id: "mid-1", severity: "warning", state: "resolved", opened_at: ago(12 * MIN), resolved_at: ago(3 * MIN), resolved_by: "user_2", resolution: "manual" }),
    ];
    renderApp(ccPage, { api: ccApi({ alerts }) });
    const panel = await screen.findByLabelText("Recent alert transitions");
    await waitFor(() => expect(within(panel).getByText("Recovered")).toBeInTheDocument());
    expect(within(panel).getByText("Resolved manually")).toBeInTheDocument();
    expect(panel).toHaveTextContent("Recovered by system");
    expect(panel).toHaveTextContent("Resolved manually by user_2");
    expect(panel).not.toHaveTextContent("Recovered by user_2");
    expect(panel).toHaveTextContent("Alert transitions only. Severity changes, agent starts and stops, and host state changes are not recorded here.");
  });

  it("lists transitions newest first", async () => {
    const alerts = [
      alertRow({ id: "old", host_id: "web-1", opened_at: ago(60 * MIN) }),
      alertRow({ id: "new", host_id: "mid-1", severity: "warning", opened_at: ago(2 * MIN) }),
    ];
    renderApp(ccPage, { api: ccApi({ alerts }) });
    const list = await screen.findByLabelText("Alert transitions, newest first");
    const items = within(list).getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items[0]).toContain("mid-1");
    expect(items[1]).toContain("web-1");
  });

  it("a capped list says it covers the newest alerts and never claims an empty window", async () => {
    const alerts = Array.from({ length: 200 }, (_, i) => alertRow({ id: `old-${i}`, host_id: "web-1", opened_at: ago(30 * 24 * 60 * MIN) }));
    renderApp(ccPage, { api: ccApi({ alerts, alertTotal: 250 }) });
    expect(await screen.findByText(/Covers the 200 newest alerts/)).toBeInTheDocument();
    expect(screen.queryByText(/No alert transitions in the last 24 hours/)).not.toBeInTheDocument();
  });

  it("a complete list with nothing in the window says so", async () => {
    const alerts = [alertRow({ id: "old", host_id: "web-1", opened_at: ago(30 * 24 * 60 * MIN) })];
    renderApp(ccPage, { api: ccApi({ alerts }) });
    expect(await screen.findByText("No alert transitions in the last 24 hours.")).toBeInTheDocument();
  });
});

describe("Command Center: connected infrastructure, scope and links (CC-2, CC-3)", () => {
  it("LIVE excluded by scope: no LIVE request is made and the line offers to show LIVE", async () => {
    const hostCalls: HostListParams[] = [];
    renderApp(ccPage, { api: ccApi({ hostCalls }), route: "/?source=synthetic" });
    expect(await screen.findByText("LIVE hidden by scope.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show LIVE" })).toBeInTheDocument();
    expect(hostCalls.some((c) => c.source?.includes("live"))).toBe(false);
  });

  it("names silent LIVE hosts and counts from the LIVE facets", async () => {
    renderApp(ccPage, { api: ccApi() });
    const panel = await screen.findByLabelText("Connected infrastructure");
    await waitFor(() => expect(panel).toHaveTextContent("4 of 6 reporting"));
    expect(panel).toHaveTextContent("1 silent");
    expect(await within(panel).findByRole("link", { name: "gone-1" })).toHaveAttribute("href", "/hosts/gone-1");
    expect(panel).not.toHaveTextContent("sim0001-x");
  });

  it("every host and alert link keeps the source scope and the time range", async () => {
    const alerts = [alertRow({ id: "a1", host_id: "web-1" })];
    renderApp(ccPage, { api: ccApi({ alerts }), route: "/?source=live,replay&range=24h" });
    await screen.findByLabelText("Hosts needing attention");
    await screen.findByLabelText("Alert counts");
    const links = Array.from(document.querySelectorAll("a")).map((a) => a.getAttribute("href") ?? "");
    const scoped = links.filter((h) => h.startsWith("/hosts") || h.startsWith("/alerts/"));
    expect(scoped.length).toBeGreaterThan(3);
    for (const href of scoped) {
      const url = new URL(href, "http://x");
      expect(url.searchParams.get("source"), href).toBe("live,replay");
      expect(url.searchParams.get("range"), href).toBe("24h");
    }
  });

  it("the attention 'View all' link keeps the same not-healthy set", async () => {
    renderApp(ccPage, { api: ccApi() });
    const table = await screen.findByLabelText("Hosts needing attention");
    const link = await within(table).findByRole("link", { name: /View all/ });
    expect(new URL(link.getAttribute("href")!, "http://x").searchParams.get("health")).toBe("critical,warning,stale,offline,unknown");
  });
});

describe("Command Center: empty and error states", () => {
  it("with no hosts in the workspace, shows the honest empty state and no panels", async () => {
    renderApp(ccPage, { api: ccApi({ hosts: [] }) });
    expect(await screen.findByText("No hosts have reported yet")).toBeInTheDocument();
    expect(screen.queryByLabelText("Active alerts")).not.toBeInTheDocument();
  });

  it("a failed summary still renders the other panels, with an honest note", async () => {
    renderApp(ccPage, { api: ccApi({ failHosts: new ApiError(0, "network_error", "x") }) });
    expect(await screen.findByText(/Overall counts are unavailable/)).toBeInTheDocument();
    expect(await screen.findByLabelText("Active alerts")).toBeInTheDocument();
  });
});
