import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError, type ApiClient } from "../../api/client";
import type { Alert, Host, Inventory, InventoryHost } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { host } from "../../test/fixtures";
import { InfrastructurePage } from "./InfrastructurePage";

const ih = (host_id: string, extra: Partial<InventoryHost> = {}): InventoryHost => ({ host_id, display_name: null, kind: "live", deactivated: false, ...extra });

const inventory: Inventory = {
  environments: [{
    id: "e-prod", name: "production",
    regions: [{
      id: "r-eu", name: "eu-west-1",
      clusters: [{ id: "c-api", name: "api", hosts: [ih("api-1", { display_name: "API one" }), ih("api-2", { kind: "synthetic" })] }],
    }],
  }],
  unassigned_regions: [],
  unassigned_clusters: [{ id: "c-batch", name: "batch", hosts: [ih("batch-1")] }],
  ungrouped_hosts: [ih("loose-1")],
  unassigned_services: [{ id: "s-metrics", name: "metrics" }],
  services: [
    { id: "s-checkout", name: "checkout", description: "Payments", hosts: [ih("api-1"), ih("batch-1")] },
    { id: "s-worker", name: "worker", description: null, hosts: [ih("api-1"), ih("loose-1")] },
    { id: "s-metrics", name: "metrics", description: null, hosts: [] },
  ],
};

const monitored: Host[] = [
  host({ host_id: "api-1", health: { status: "healthy", category: "healthy", reasons: [] } }),
  host({ host_id: "api-2", source: "synthetic", health: { status: "critical", category: "degraded", reasons: [] } }),
  host({ host_id: "loose-1", health: { status: "stale", category: "stale", reasons: [] } }),
];

const alerts: Alert[] = [
  {
    id: "al-1", host_id: "api-2", condition_key: "k1", rule: "threshold", metric: "system.cpu.utilization", labels: {}, severity: "critical",
    state: "open", value: 97, threshold: 95, opened_at: "2026-11-13T10:00:00Z", last_evaluated_at: "2026-11-13T10:00:00Z",
    acknowledged_at: null, acknowledged_by: null, resolved_at: null, resolved_by: null, resolution: null,
  },
];

function api(over: Partial<ApiClient> = {}) {
  return fakeApi({
    infrastructure: async () => inventory,
    hosts: async (p?: { page?: number }) => (p?.page ?? 1) === 1
      ? { items: monitored, page: 1, page_size: 200, total: monitored.length, facets: { source: { live: 2, synthetic: 1, replay: 0 }, health: { healthy: 1, warning: 0, critical: 1, stale: 1, offline: 0, unknown: 0 } }, thresholds: { host_stale_after_seconds: 90, offline_after_seconds: 300, stale_after_seconds: 300, rules: {} } }
      : { items: [], page: p?.page ?? 2, page_size: 200, total: monitored.length, facets: { source: { live: 0, synthetic: 0, replay: 0 }, health: { healthy: 0, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0 } }, thresholds: { host_stale_after_seconds: 90, offline_after_seconds: 300, stale_after_seconds: 300, rules: {} } },
    alerts: async () => ({ alerts, total: alerts.length }),
    ...over,
  } as Partial<ApiClient>);
}

const rowOf = (nodeId: string) => document.querySelector(`[data-node-id="${nodeId}"]`) as HTMLElement;
const containment = () => screen.findByLabelText("Containment");

async function open(over: Partial<ApiClient> = {}) {
  const r = renderApp(<InfrastructurePage />, { api: api(over), route: "/infrastructure" });
  await containment();
  return r;
}

describe("topology: hierarchy and states", () => {
  it("shows the saved hierarchy, the ungrouped host and the unassigned cluster rather than hiding them", async () => {
    await open();
    const c = await containment();
    expect(within(c).getByText("production")).toBeInTheDocument();
    expect(within(c).getByText("eu-west-1")).toBeInTheDocument();
    expect(within(c).getByText("api")).toBeInTheDocument();
    expect(within(c).getByText("batch")).toBeInTheDocument();
    expect(within(c).getByText("Hosts not in a cluster")).toBeInTheDocument();
    expect(within(rowOf("host:loose-1")).getByText("loose-1")).toBeInTheDocument();
  });

  it("opens on the topology tab, with manage available as the other tab", async () => {
    await open();
    expect(screen.getByRole("tab", { name: "Topology" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Manage" })).toHaveAttribute("aria-selected", "false");
  });

  it("shows each host's backend health, and the source, without inventing a state for hosts with no data", async () => {
    await open();
    expect(within(rowOf("host:api-1")).getByText("HEALTHY")).toBeInTheDocument();
    expect(within(rowOf("host:api-2")).getByText("CRITICAL")).toBeInTheDocument();
    const unloaded = rowOf("host:batch-1");
    expect(within(unloaded).getByText("no data loaded")).toBeInTheDocument();
    expect(within(unloaded).queryByText("HEALTHY")).toBeNull();
  });

  it("marks a host with an active alert, and no other host", async () => {
    await open();
    const indicators = screen.getAllByTestId("host-alert");
    expect(indicators).toHaveLength(1);
    expect(within(rowOf("host:api-2")).getByTestId("host-alert")).toHaveTextContent("1 critical alert");
    expect(rowOf("host:api-1").querySelector("[data-testid='host-alert']")).toBeNull();
    expect(rowOf("host:loose-1").querySelector("[data-testid='host-alert']")).toBeNull();
  });

  it("states that a service is not a dependency on its hosts and lists the hosts by name", async () => {
    await open();
    fireEvent.click(within(screen.getByLabelText("Services")).getByRole("button", { name: /checkout/ }));
    const panel = screen.getByLabelText("Details");
    expect(within(panel).getByText(/not verified dependencies/)).toBeInTheDocument();
    expect(within(panel).getByText("API one")).toBeInTheDocument();
    expect(within(panel).getByText("batch-1")).toBeInTheDocument();
    expect(within(panel).getByText("no data loaded")).toBeInTheDocument();
  });

  it("shows a service with no hosts as unassigned", async () => {
    await open();
    expect(within(screen.getByLabelText("Services")).getByText("No hosts assigned")).toBeInTheDocument();
  });
});

describe("topology: interaction", () => {
  it("expands and collapses a cluster and keeps the control's state in sync", async () => {
    await open();
    const toggle = screen.getByRole("button", { name: "Collapse api" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Expand api" })).toHaveAttribute("aria-expanded", "false");
    expect(rowOf("host:api-2")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Expand api" }));
    expect(rowOf("host:api-2")).not.toBeNull();
  });

  it("selecting a host shows its health, membership, services and alerts in the details panel", async () => {
    await open();
    fireEvent.click(within(rowOf("host:api-2")).getByRole("button", { name: /api-2/ }));
    const panel = screen.getByLabelText("Details");
    expect(within(panel).getByRole("heading", { name: "api-2" })).toBeInTheDocument();
    expect(within(panel).getByText("production › eu-west-1 › api")).toBeInTheDocument();
    expect(within(panel).getByText("None assigned in the inventory")).toBeInTheDocument();
    expect(within(panel).getByText(/Threshold breach/)).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Open alert" })).toHaveAttribute("href", "/alerts/al-1");
  });

  it("links every host to its existing Host Detail page", async () => {
    await open();
    expect(screen.getByRole("link", { name: "Open host detail for API one" })).toHaveAttribute("href", "/hosts/api-1");
    fireEvent.click(within(rowOf("host:api-1")).getByRole("button", { name: /API one/ }));
    expect(within(screen.getByLabelText("Details")).getByRole("link", { name: "Open host detail" })).toHaveAttribute("href", "/hosts/api-1");
  });

  it("selecting a cluster shows what it contains by actual state, with no invented group health", async () => {
    await open();
    fireEvent.click(within(await containment()).getByRole("button", { name: /api\s+2 hosts/ }));
    const panel = screen.getByLabelText("Details");
    expect(within(panel).getByText("2 hosts in this cluster.")).toBeInTheDocument();
    expect(within(panel).getByText("Active alerts on these hosts: 1")).toBeInTheDocument();
    expect(within(panel).getByText(/no health status of its own/)).toBeInTheDocument();
  });

  it("draws one line per service and target, and re-anchors lines to a collapsed cluster", async () => {
    await open();
    const edges = () => screen.getAllByTestId("topo-edge").map((p) => [p.getAttribute("data-service"), p.getAttribute("data-target")]);
    expect(edges()).toEqual(expect.arrayContaining([
      ["svc:s-checkout", "host:api-1"], ["svc:s-checkout", "host:batch-1"], ["svc:s-worker", "host:api-1"], ["svc:s-worker", "host:loose-1"],
    ]));
    fireEvent.click(screen.getByRole("button", { name: "Collapse api" }));
    const after = edges();
    expect(after).toEqual(expect.arrayContaining([["svc:s-checkout", "cluster:c-api"], ["svc:s-worker", "cluster:c-api"]]));
    expect(after.some(([, t]) => t === "host:api-1")).toBe(false);
  });

  it("the Manage tab shows the existing editor", async () => {
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "Manage" }));
    expect(await screen.findByLabelText("Create an environment")).toBeInTheDocument();
    expect(screen.queryByLabelText("Containment")).toBeNull();
  });
});

describe("topology: empty and degraded data", () => {
  it("shows a useful empty state when no infrastructure has been configured", async () => {
    renderApp(<InfrastructurePage />, { api: api({ infrastructure: async () => ({ environments: [], unassigned_regions: [], unassigned_clusters: [], ungrouped_hosts: [], services: [], unassigned_services: [] }), hosts: async () => ({ items: [], page: 1, page_size: 200, total: 0, facets: { source: { live: 0, synthetic: 0, replay: 0 }, health: { healthy: 0, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0 } }, thresholds: { host_stale_after_seconds: 90, offline_after_seconds: 300, stale_after_seconds: 300, rules: {} } }), alerts: async () => ({ alerts: [], total: 0 }) }), route: "/infrastructure" });
    expect(await screen.findByText("No infrastructure configured yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Organise infrastructure" }));
    expect(await screen.findByLabelText("Create an environment")).toBeInTheDocument();
  });

  it("says so when only part of the host health could be loaded", async () => {
    await open({
      hosts: async (p?: { page?: number }) => (p?.page ?? 1) === 1
        ? { items: [monitored[0]!], page: 1, page_size: 200, total: 3, facets: { source: { live: 1, synthetic: 0, replay: 0 }, health: { healthy: 1, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0 } }, thresholds: { host_stale_after_seconds: 90, offline_after_seconds: 300, stale_after_seconds: 300, rules: {} } }
        : { items: [], page: 2, page_size: 200, total: 3, facets: { source: { live: 0, synthetic: 0, replay: 0 }, health: { healthy: 0, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0 } }, thresholds: { host_stale_after_seconds: 90, offline_after_seconds: 300, stale_after_seconds: 300, rules: {} } },
    });
    expect(await screen.findByText(/loaded for 1 of 3 hosts/)).toBeInTheDocument();
  });

  it("shows the structure with an error when monitoring data fails, instead of failing the page", async () => {
    await open({ hosts: async () => { throw new ApiError(503, "unavailable", "x"); } });
    expect(await screen.findByText(/Monitoring data could not be loaded/)).toBeInTheDocument();
    expect(within(rowOf("host:api-1")).getByText("no data loaded")).toBeInTheDocument();
    expect(within(await containment()).getByText("production")).toBeInTheDocument();
  });

  it("says alert indicators are unavailable when the alert list fails", async () => {
    await open({ alerts: async () => { throw new ApiError(503, "unavailable", "x"); } });
    expect(await screen.findByText(/Active alerts could not be loaded/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryAllByTestId("host-alert")).toHaveLength(0));
  });
});
