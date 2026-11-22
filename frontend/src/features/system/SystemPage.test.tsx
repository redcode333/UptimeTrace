import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { HostListParams, McpToken, MonitoringSettings, PlatformHealth, RegistryHost } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { host, hostList } from "../../test/fixtures";
import { SystemPage } from "./SystemPage";

const OK: PlatformHealth = { http: 200, status: "ok", app: "ok", database: "ok" };
const DEGRADED: PlatformHealth = { http: 503, status: "degraded", app: "ok", database: "unavailable" };

const DEFAULTS: MonitoringSettings["config"] = {
  freshness: { stale_after_seconds: 90, offline_after_seconds: 300 },
  rules: {
    "system.cpu.utilization": { warning: 85, critical: 95 },
    "system.memory.utilization": { warning: 85, critical: 95 },
    "system.disk.utilization": { warning: 90, critical: 95 },
  },
};
const SETTINGS: MonitoringSettings = { config: DEFAULTS, defaults: DEFAULTS, version: 0, updated_at: null, updated_by: null };

const token = (over: Partial<McpToken> & { id: string }): McpToken => ({
  name: "laptop", scopes: ["read"], created_at: "2026-11-01T09:00:00Z", created_by: "user_test_1",
  expires_at: "2099-01-01T00:00:00Z", revoked_at: null, revoked_by: null, ...over,
});

const live = host({ host_id: "web-1", hostname: "web-1", source: "live", health: { status: "healthy", category: "healthy", reasons: [] }, last_seen: "2026-11-20T10:00:00Z" });
const sim = host({ host_id: "sim01-a", hostname: "sim01-a", source: "synthetic", health: { status: "critical", category: "degraded", reasons: [] } });

function api(over: Record<string, unknown> = {}) {
  return fakeApi({
    platformHealth: async () => OK,
    hosts: async (p: HostListParams = {}) => {
      const pool = p.source ? [live, sim].filter((h) => p.source!.includes(h.source)) : [live, sim];
      return hostList(pool.slice(0, p.page_size ?? 50), {
        total: pool.length,
        facets: {
          source: { live: 1, synthetic: 1, replay: 0 },
          health: { healthy: 1, warning: 0, critical: 1, stale: 0, offline: 0, unknown: 0 },
        },
      });
    },
    registryHosts: async () => [] as RegistryHost[],
    monitoringSettings: async () => SETTINGS,
    mcpTokens: async () => [] as McpToken[],
    ...over,
  });
}

describe("System: platform status", () => {
  it("reports both checked components as reachable when the probe says so", async () => {
    renderApp(<SystemPage />, { api: api() });
    expect(await screen.findByText("Both checked components are reachable.")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Platform checks" })).toHaveTextContent("Responding");
    expect(screen.getByRole("table", { name: "Platform checks" })).toHaveTextContent("Reachable");
  });

  it("reports a degraded database as degraded, not healthy", async () => {
    renderApp(<SystemPage />, { api: api({ platformHealth: async () => DEGRADED }) });
    expect(await screen.findByText(/Degraded: a checked dependency is unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Platform checks" })).toHaveTextContent("Unavailable");
    expect(screen.queryByText("Both checked components are reachable.")).toBeNull();
  });

  it("treats a failed probe as unavailable, never as healthy", async () => {
    renderApp(<SystemPage />, { api: api({ platformHealth: async () => { throw new ApiError(0, "network_error", "Could not reach the UptimeTrace API."); } }) });
    expect(await screen.findByRole("alert")).toHaveTextContent("Unavailable: the health check did not answer");
    expect(screen.queryByText("Both checked components are reachable.")).toBeNull();
    expect(screen.queryByText("Responding")).toBeNull();
  });

  it("labels agents, trace backend and collector as not reported, not as available", async () => {
    renderApp(<SystemPage />, { api: api() });
    await screen.findByText("Both checked components are reachable.");
    expect(screen.getByRole("table", { name: "Platform checks" })).toHaveTextContent("Not reported by the server");
    const others = screen.getByRole("table", { name: "Components the server does not report" });
    expect(within(others).getByText("Trace backend (Jaeger)")).toBeInTheDocument();
    expect(within(others).getByText("Collector")).toBeInTheDocument();
    expect(within(others).getAllByText("Unknown").length).toBe(2);
  });

  it("checks again when asked", async () => {
    const probe = vi.fn(async () => OK);
    renderApp(<SystemPage />, { api: api({ platformHealth: probe }) });
    await screen.findByText("Both checked components are reachable.");
    fireEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(probe.mock.calls.length).toBeGreaterThanOrEqual(2));
  });
});

describe("System: agents and telemetry", () => {
  it("shows host health counts and the source of each host, without merging sources", async () => {
    renderApp(<SystemPage />, { api: api() });
    const counts = await screen.findByRole("table", { name: "Host health counts" });
    expect(within(counts).getByText("CRITICAL").closest("tr")).toHaveTextContent("1");
    expect(screen.getByLabelText("Hosts by source")).toHaveTextContent("LIVE");
    expect(screen.getByLabelText("Hosts by source")).toHaveTextContent("SYNTHETIC");
  });

  it("names the newest telemetry in scope and links to the host", async () => {
    renderApp(<SystemPage />, { api: api() });
    const link = await screen.findByRole("link", { name: "web-1" });
    expect(link).toHaveAttribute("href", "/hosts/web-1");
  });

  it("says when no host in scope has sent telemetry", async () => {
    renderApp(<SystemPage />, {
      api: api({ hosts: async () => hostList([], { total: 0 }) }),
    });
    expect(await screen.findByText("no host in scope has sent telemetry yet")).toBeInTheDocument();
  });

  it("counts registered hosts only for the selected sources", async () => {
    const regs: RegistryHost[] = [
      { host_id: "web-1", kind: "live", display_name: null, created_at: "2026-11-01T09:00:00Z", created_by: "u", deactivated_at: null, telemetry_state: "reporting", last_envelope_at: null, active_credentials: 1 },
      { host_id: "new-1", kind: "live", display_name: null, created_at: "2026-11-01T09:00:00Z", created_by: "u", deactivated_at: null, telemetry_state: "awaiting_first_telemetry", last_envelope_at: null, active_credentials: 1 },
      { host_id: "sim-x", kind: "synthetic", display_name: null, created_at: "2026-11-01T09:00:00Z", created_by: "u", deactivated_at: null, telemetry_state: "reporting", last_envelope_at: null, active_credentials: 1 },
    ];
    renderApp(<SystemPage />, { api: api({ registryHosts: async () => regs }), route: "/system?source=live" });
    const table = await screen.findByRole("table", { name: "Registered host states" });
    expect(within(table).getByText("Registered, never reported").closest("tr")).toHaveTextContent("1");
    // one live host is reporting; the synthetic host is outside the selected source and must not be counted
    expect(within(table).getByText("Reporting").closest("tr")).toHaveTextContent("1");
  });
});

describe("System: configuration and integrations", () => {
  it("shows the configured limits and says they match the defaults", async () => {
    renderApp(<SystemPage />, { api: api() });
    const t = await screen.findByRole("table", { name: "Configured thresholds" });
    expect(t).toHaveTextContent("90 s");
    expect(t).toHaveTextContent("85% / 95%");
    expect(screen.getByText(/matches the defaults/)).toBeInTheDocument();
  });

  it("says when the limits differ from the defaults", async () => {
    const changed: MonitoringSettings = { ...SETTINGS, version: 3, updated_at: "2026-11-10T09:00:00Z", config: { ...DEFAULTS, freshness: { stale_after_seconds: 60, offline_after_seconds: 300 } } };
    renderApp(<SystemPage />, { api: api({ monitoringSettings: async () => changed }) });
    expect(await screen.findByText(/differs from the defaults/)).toBeInTheDocument();
  });

  it("counts active access tokens without showing any token value", async () => {
    const tokens = [token({ id: "t1", name: "laptop" }), token({ id: "t2", name: "old", revoked_at: "2026-11-02T00:00:00Z", revoked_by: "u" })];
    renderApp(<SystemPage />, { api: api({ mcpTokens: async () => tokens }) });
    expect(await screen.findByText(/1 active of 20 allowed/)).toBeInTheDocument();
    expect(screen.getByText(/Token values and their hashes are never shown/)).toBeInTheDocument();
  });

  it("says the MCP endpoint's reachability is not reported, rather than claiming it", async () => {
    renderApp(<SystemPage />, { api: api() });
    expect(await screen.findByText(/does not report whether the MCP endpoint is reachable/)).toBeInTheDocument();
  });

  it("shows a failed configuration read as unavailable, not as defaults", async () => {
    renderApp(<SystemPage />, { api: api({ monitoringSettings: async () => { throw new Error("nope"); } }) });
    expect(await screen.findByText("The configured limits could not be read.")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Configured thresholds" })).toBeNull();
  });
});
