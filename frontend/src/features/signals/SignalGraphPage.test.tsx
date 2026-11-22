import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../../api/client";
import type { Host, HostList, LatestMetrics } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { SignalGraphPage } from "./SignalGraphPage";

function hostListOf(items: Host[]): HostList {
  return {
    items,
    page: 1,
    page_size: 200,
    total: items.length,
    facets: { source: { live: items.length, synthetic: 0, replay: 0 }, health: { healthy: 0, warning: 0, critical: 0, offline: 0, unknown: 0 } } as HostList["facets"],
    thresholds: { host_stale_after_seconds: 120, offline_after_seconds: 300, stale_after_seconds: 60, rules: {} },
  } as HostList;
}

function hostFixture(id: string, name: string): Host {
  return {
    host_id: id, hostname: name, agent_id: `agent-${id}`, service: null, source: "live", connected_infrastructure: false,
    first_seen: "2026-11-20T11:00:00Z", last_seen: new Date().toISOString(), last_seen_age_seconds: 1, reporting_status: "reporting",
    health: "healthy", latest: {} as Host["latest"],
  } as unknown as Host;
}

function latestOf(hostId: string, cpu: number): LatestMetrics {
  const now = new Date().toISOString();
  return {
    host_id: hostId,
    source: "live",
    last_seen: now,
    metrics: [{ name: "system.cpu.utilization", kind: "gauge", unit: "percent", labels: {}, value: cpu, observed_at: now, stale: false, rate_per_second: null }],
  };
}

describe("Signal Graph page", () => {
  it("says so plainly when no host is registered, and draws no graph", async () => {
    renderApp(<SignalGraphPage />, { api: fakeApi({ hosts: async () => hostListOf([]) }) });
    expect(await screen.findByText("No hosts to draw yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /CPU/ })).toBeNull();
  });

  it("draws a block only for a component the agent actually reported, with its real value", async () => {
    renderApp(<SignalGraphPage />, {
      api: fakeApi({
        hosts: async () => hostListOf([hostFixture("h1", "api-1")]),
        latest: async (id: string) => latestOf(id, 42),
      }) as ApiClient,
    });
    const block = await screen.findByRole("button", { name: /api-1 CPU: flowing, last reading 42 percent/ });
    expect(block).toHaveTextContent("42%");
    // the graph draws no dependency or call between hosts: nothing is inferred
    expect(screen.queryByText(/depends on|calls|upstream/i)).toBeNull();
  });

  it("shows an unknown lane when the latest read fails, instead of a silent empty host", async () => {
    renderApp(<SignalGraphPage />, {
      api: fakeApi({
        hosts: async () => hostListOf([hostFixture("h1", "db-1")]),
        latest: async () => { throw new Error("boom"); },
      }) as ApiClient,
    });
    expect(await screen.findByText("Latest read unavailable")).toBeInTheDocument();
  });

  it("states what the agent does not collect and keeps the history controls honestly unavailable", async () => {
    renderApp(<SignalGraphPage />, {
      api: fakeApi({
        hosts: async () => hostListOf([hostFixture("h1", "api-1")]),
        latest: async (id: string) => latestOf(id, 5),
      }) as ApiClient,
    });
    await screen.findByRole("button", { name: /api-1 CPU/ });
    const panel = screen.getByRole("region", { name: "Signal Graph" });
    expect(within(panel).getByText("Disk I/O")).toBeInTheDocument();
    expect(within(panel).getAllByText("not collected").length).toBeGreaterThan(0);
    const oneHour = within(panel).getByRole("button", { name: "1h" });
    expect(oneHour).toBeDisabled();
    expect(oneHour).toHaveAttribute("title", expect.stringMatching(/not part of this graph/));
  });

  it("switches between the 3D view and the flat view", async () => {
    renderApp(<SignalGraphPage />, {
      api: fakeApi({
        hosts: async () => hostListOf([hostFixture("h1", "api-1")]),
        latest: async (id: string) => latestOf(id, 5),
      }) as ApiClient,
    });
    await screen.findByRole("button", { name: /api-1 CPU/ });
    const flat = screen.getByRole("button", { name: "Flat" });
    fireEvent.click(flat);
    expect(flat).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "3D" })).toHaveAttribute("aria-pressed", "false");
  });
});

describe("Signal Graph: inspection, scope and coverage", () => {
  it("opens the selected thread's real sample, with links to its host and its metric's history", async () => {
    renderApp(<SignalGraphPage />, {
      api: fakeApi({
        hosts: async () => hostListOf([hostFixture("h1", "api-1")]),
        latest: async (id: string) => latestOf(id, 42),
      }) as ApiClient,
    });
    fireEvent.click(await screen.findByRole("button", { name: /api-1 CPU: flowing, last reading 42 percent/ }));
    const inspector = screen.getByLabelText("Selected thread");
    expect(within(inspector).getByText("cpu utilisation")).toBeInTheDocument();
    expect(within(inspector).getByRole("link", { name: "Open host" })).toHaveAttribute("href", "/hosts/h1");
    expect(within(inspector).getByRole("link", { name: /Metrics Explorer/ })).toHaveAttribute(
      "href",
      "/metrics?host=h1&metric=system.cpu.utilization",
    );
  });

  it("clears the selection from the inspector", async () => {
    renderApp(<SignalGraphPage />, {
      api: fakeApi({
        hosts: async () => hostListOf([hostFixture("h1", "api-1")]),
        latest: async (id: string) => latestOf(id, 42),
      }) as ApiClient,
    });
    fireEvent.click(await screen.findByRole("button", { name: /api-1 CPU/ }));
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.queryByLabelText("Selected thread")).toBeNull();
  });

  it("asks the server only for hosts in the selected source scope", async () => {
    const hosts = vi.fn(async () => hostListOf([hostFixture("h1", "api-1")]));
    renderApp(<SignalGraphPage />, {
      api: fakeApi({ hosts, latest: async (id: string) => latestOf(id, 5) }) as ApiClient,
      route: "/signal-graph?source=synthetic",
    });
    await screen.findByRole("button", { name: /api-1 CPU/ });
    expect((hosts.mock.calls[0] as unknown[])[0]).toMatchObject({ source: ["synthetic"] });
  });

  it("says how many hosts are not drawn when the list is larger than one page", async () => {
    const page = { ...hostListOf([hostFixture("h1", "api-1")]), total: 340 } as HostList;
    renderApp(<SignalGraphPage />, {
      api: fakeApi({ hosts: async () => page, latest: async (id: string) => latestOf(id, 5) }) as ApiClient,
    });
    expect(await screen.findByText(/Showing the first 1 of 340 hosts in scope/)).toBeInTheDocument();
  });
});
