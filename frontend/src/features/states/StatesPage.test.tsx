import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Alert, AlertList, AlertStatusFilter, HealthStatus, Host, HostList, HostListParams } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { host, hostList } from "../../test/fixtures";
import { StatesPage } from "./StatesPage";

/** The row of a counts table whose state label matches. Labels are rendered upper-case, as the badges are. */
const rowFor = (table: HTMLElement, label: string) => {
  const row = Array.from(table.querySelectorAll("tbody tr")).find((r) => r.textContent?.includes(label));
  if (!row) throw new Error(`no row for ${label}`);
  return row as HTMLElement;
};

const health = (status: HealthStatus, reasons: Host["health"]["reasons"] = []): Host["health"] => ({ status, category: "healthy", reasons });
const live = host({ host_id: "web-1", hostname: "web-1", source: "live", health: health("critical") });
const sim = host({ host_id: "sim01-a", hostname: "sim01-a", source: "synthetic", health: health("critical") });
const ok = host({ host_id: "db-1", hostname: "db-1", source: "live", health: health("healthy") });

const FACETS = {
  source: { live: 2, synthetic: 1, replay: 0 },
  health: { healthy: 1, warning: 0, critical: 2, stale: 0, offline: 0, unknown: 0 },
};
const THRESHOLDS = {
  host_stale_after_seconds: 90, offline_after_seconds: 300, stale_after_seconds: 300,
  rules: {
    "system.cpu.utilization": { warning: 85, critical: 95 },
    "system.memory.utilization": { warning: 85, critical: 95 },
    "system.disk.utilization": { warning: 90, critical: 95 },
  },
};

function hostsApi(items: Host[], calls: HostListParams[] = [], over: Partial<HostList> = {}) {
  return async (p: HostListParams = {}) => {
    calls.push(p);
    const pool = p.source ? items.filter((h) => p.source!.includes(h.source)) : items;
    const filtered = p.health ? pool.filter((h) => p.health!.includes(h.health.status)) : pool;
    return hostList(filtered.slice(0, p.page_size ?? 50), {
      total: filtered.length, facets: { source: FACETS.source, health: FACETS.health }, thresholds: THRESHOLDS, ...over,
    }) as HostList;
  };
}

const alert = (over: Partial<Alert> & { id: string }): Alert => ({
  host_id: "web-1", condition_key: "system.cpu.utilization|{}", rule: "threshold", metric: "system.cpu.utilization", labels: {},
  severity: "critical", state: "open", value: 97, threshold: 95, opened_at: "2026-11-11T12:00:00Z", last_evaluated_at: "2026-11-11T12:10:00Z",
  acknowledged_at: null, acknowledged_by: null, resolved_at: null, resolved_by: null, resolution: null, ...over,
});

function alertsApi(all: Alert[], total?: number, calls: { status: AlertStatusFilter }[] = []) {
  return async (status: AlertStatusFilter = "active"): Promise<AlertList> => {
    calls.push({ status });
    const pool = status === "active" ? all.filter((a) => a.state !== "resolved") : status === "resolved" ? all.filter((a) => a.state === "resolved") : all;
    return { alerts: pool, total: total ?? pool.length };
  };
}

describe("Operational states: host health", () => {
  it("counts each health state for the workspace, with what it means", async () => {
    renderApp(<StatesPage />, { api: fakeApi({ hosts: hostsApi([live, sim, ok]), alerts: alertsApi([]) }) });
    const table = await screen.findByRole("table", { name: "Host health counts" });
    expect(rowFor(table, "HEALTHY")).toHaveTextContent("1");
    expect(rowFor(table, "CRITICAL")).toHaveTextContent("2");
    expect(rowFor(table, "UNKNOWN")).toHaveTextContent("0");
    expect(within(table).getByText(/A missing value is never treated as zero/)).toBeInTheDocument();
  });

  it("shows the hosts of the chosen state and links each to its host page", async () => {
    renderApp(<StatesPage />, { api: fakeApi({ hosts: hostsApi([live, sim, ok]), alerts: alertsApi([]) }), route: "/states?health=critical" });
    const list = await screen.findByRole("table", { name: "CRITICAL hosts" });
    expect(within(list).getByRole("link", { name: "web-1" })).toHaveAttribute("href", "/hosts/web-1");
    expect(within(list).queryByRole("link", { name: "db-1" })).toBeNull();
  });

  it("switches the listed state from the counts table, and the request carries that health", async () => {
    const calls: HostListParams[] = [];
    renderApp(<StatesPage />, { api: fakeApi({ hosts: hostsApi([live, sim, ok], calls), alerts: alertsApi([]) }), route: "/states" });
    await screen.findByRole("table", { name: "CRITICAL hosts" });
    const healthy = screen.getAllByRole("button", { name: "Show hosts" })[0]!;
    fireEvent.click(healthy);
    await waitFor(() => expect(calls.at(-1)?.health).toEqual(["healthy"]));
    expect(await screen.findByRole("table", { name: "HEALTHY hosts" })).toBeInTheDocument();
  });

  it("says when a state has no hosts in this scope, rather than showing an empty table", async () => {
    renderApp(<StatesPage />, { api: fakeApi({ hosts: hostsApi([ok]), alerts: alertsApi([]) }), route: "/states?health=critical" });
    expect(await screen.findByText("No critical hosts in this scope")).toBeInTheDocument();
  });

  it("states that the list is partial and where the rest are", async () => {
    const many = Array.from({ length: 60 }, (_, i) => host({ host_id: `c-${i}`, hostname: `c-${i}`, source: "live", health: health("critical") }));
    renderApp(<StatesPage />, { api: fakeApi({ hosts: hostsApi(many, [], { total: 60 }), alerts: alertsApi([]) }), route: "/states?health=critical" });
    expect(await screen.findByText(/Showing 50 of 60/)).toBeInTheDocument();
  });

  it("counts follow the selected source, and a failed count is shown as unavailable, never as zero", async () => {
    const calls: HostListParams[] = [];
    renderApp(<StatesPage />, { api: fakeApi({ hosts: hostsApi([live, sim, ok], calls), alerts: alertsApi([]) }), route: "/states?source=synthetic" });
    await screen.findByRole("table", { name: "CRITICAL hosts" });
    expect(calls.some((c) => JSON.stringify(c.source) === '["synthetic"]')).toBe(true);
  });

  it("shows a failed host count as an error, not as zero hosts", async () => {
    const failing = fakeApi({
      hosts: async (p: HostListParams = {}) => {
        if (p.page_size === 1) throw new Error("count down");
        return hostsApi([live])(p);
      },
      alerts: alertsApi([]),
    });
    renderApp(<StatesPage />, { api: failing });
    await waitFor(() => expect(screen.queryByText(/Counting hosts/)).not.toBeInTheDocument());
    expect(screen.queryByRole("table", { name: "Host health counts" })).toBeNull();
  });
});

describe("Operational states: alert lifecycle", () => {
  it("counts active and resolved alerts from the server, with the open and acknowledged split", async () => {
    const acked = alert({ id: "a2", state: "acknowledged", severity: "warning", acknowledged_by: "user_test_1" });
    const resolvedRow = alert({ id: "a3", state: "resolved", resolution: "recovered", resolved_at: "2026-11-11T12:30:00Z" });
    renderApp(<StatesPage />, { api: fakeApi({ hosts: hostsApi([live, ok]), alerts: alertsApi([alert({ id: "a1" }), acked, resolvedRow]) }), route: "/states" });
    const table = await screen.findByRole("table", { name: "Alert lifecycle counts" });
    expect(within(table).getByText("Active, total").closest("tr")).toHaveTextContent("2");
    expect(within(table).getByText("Resolved, total").closest("tr")).toHaveTextContent("1");
  });

  it("links each alert to its investigation, and only alerts on hosts in the selected sources are listed", async () => {
    const onSim = alert({ id: "a-sim", host_id: "sim01-a" });
    const onLive = alert({ id: "a-live", host_id: "web-1" });
    renderApp(<StatesPage />, { api: fakeApi({ hosts: hostsApi([live, sim]), alerts: alertsApi([onSim, onLive]) }), route: "/states?source=live" });
    const table = await screen.findByRole("table", { name: "Alerts in this scope" });
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getByRole("link", { name: "Open investigation" })).toHaveAttribute("href", "/alerts/a-live");
    expect(screen.queryByText("sim01-a")).toBeNull();
  });

  it("says how many loaded alerts belong to hosts that are not loaded, instead of placing them", async () => {
    const orphan = alert({ id: "a-orphan", host_id: "gone-1" });
    // With every source selected an alert on an unloaded host is kept; only a narrowed scope needs placing it.
    renderApp(<StatesPage />, { api: fakeApi({ hosts: hostsApi([live]), alerts: alertsApi([orphan]) }), route: "/states?source=live" });
    expect(await screen.findByText(/cannot be placed in the selected sources/)).toBeInTheDocument();
  });

  it("switches the alert list's status on the server", async () => {
    const calls: { status: AlertStatusFilter }[] = [];
    renderApp(<StatesPage />, { api: fakeApi({ hosts: hostsApi([live]), alerts: alertsApi([alert({ id: "a1" })], undefined, calls) }), route: "/states" });
    await screen.findByRole("table", { name: "Alerts in this scope" });
    fireEvent.click(screen.getByRole("button", { name: "Resolved" }));
    await waitFor(() => expect(calls.at(-1)?.status).toBe("resolved"));
  });

  it("states that open and acknowledged counts are for loaded alerts when the workspace holds more, and keeps the total exact", async () => {
    renderApp(<StatesPage />, { api: fakeApi({ hosts: hostsApi([live]), alerts: alertsApi([alert({ id: "a1" })], 450) }), route: "/states" });
    expect(await screen.findByText(/most recent active alerts loaded; the workspace holds 450/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Alert lifecycle counts" }).textContent).toContain("450");
  });

  it("explains an empty alert list for the chosen status", async () => {
    renderApp(<StatesPage />, { api: fakeApi({ hosts: hostsApi([live]), alerts: alertsApi([]) }), route: "/states" });
    expect(await screen.findByText("No active alerts in this scope")).toBeInTheDocument();
  });
});

describe("Operational states: rules in force", () => {
  it("states the transitions with the configured limits", async () => {
    renderApp(<StatesPage />, { api: fakeApi({ hosts: hostsApi([live]), alerts: alertsApi([]) }), route: "/states" });
    expect(await screen.findByText(/warning at 85%, critical at 95%/)).toBeInTheDocument();
    expect(screen.getByText(/never stale or offline by the clock/)).toBeInTheDocument();
  });
});
