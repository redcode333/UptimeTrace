import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { HostListParams } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { host, hostList } from "../../test/fixtures";
import { HostsPage } from "./HostsPage";

function api(calls: HostListParams[], total = 60) {
  return fakeApi({
    hosts: async (p = {}) => {
      calls.push(p);
      const first = ((p.page ?? 1) - 1) * 25;
      const items = Array.from({ length: Math.min(25, total - first) }, (_, i) =>
        host({
          host_id: `h${first + i}`,
          source: i % 2 ? "synthetic" : "live",
          latest: { cpu_percent: i === 0 ? null : 40, memory_percent: 50, disk_percent_max: 60, disk_mount_max: "C:", network_rx_bytes_per_second: 1500, network_tx_bytes_per_second: null },
        }));
      return hostList(items, { total, page: p.page ?? 1 });
    },
  });
}

describe("Hosts inventory", () => {
  it("sends backend pagination; Next requests the next page", async () => {
    const calls: HostListParams[] = [];
    renderApp(<HostsPage />, { api: api(calls) });
    await screen.findByText("Page 1 of 3 · showing 1–25 of 60");
    expect(calls[0]).toMatchObject({ page: 1, page_size: 25, sort: "last_seen", order: "desc" });
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Page 2 of 3 · showing 26–50 of 60");
    expect(calls.at(-1)).toMatchObject({ page: 2 });
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();
  });

  it("uses the global source scope as the backend source filter, with no second source filter", async () => {
    const calls: HostListParams[] = [];
    renderApp(<HostsPage />, { api: api(calls), route: "/hosts?source=synthetic,replay" });
    await screen.findByText(/Page 1 of/);
    expect(calls.every((c) => c.source?.join() === "synthetic,replay")).toBe(true);
    expect(screen.queryByRole("group", { name: /source/i })).not.toBeInTheDocument();
  });

  it("health filter and sort change the request and reset to page 1", async () => {
    const calls: HostListParams[] = [];
    renderApp(<HostsPage />, { api: api(calls), route: "/hosts?page=2" });
    await screen.findByText(/Page 2 of 3/);
    await userEvent.click(screen.getByRole("checkbox", { name: /critical/i }));
    await waitFor(() => expect(calls.at(-1)).toMatchObject({ health: ["critical"], page: 1 }));
    await userEvent.selectOptions(screen.getByLabelText("Sort"), "hostname:asc");
    await waitFor(() => expect(calls.at(-1)).toMatchObject({ sort: "hostname", order: "asc" }));
  });

  it("debounces search into a single request", async () => {
    const calls: HostListParams[] = [];
    renderApp(<HostsPage />, { api: api(calls) });
    await screen.findByText(/Page 1 of/);
    const before = calls.length;
    await userEvent.type(screen.getByRole("searchbox"), "web");
    await waitFor(() => expect(calls.at(-1)).toMatchObject({ search: "web" }));
    expect(calls.length - before).toBe(1);
  });

  it("shows missing metrics as a dash, never 0, and labels the source", async () => {
    renderApp(<HostsPage />, { api: api([]) });
    const rows = await screen.findAllByRole("row");
    const first = rows[1]!;
    expect(within(first).getAllByText("—").length).toBeGreaterThan(0);
    expect(first).toHaveTextContent("1.5 KB/s / —");
    expect(first.querySelectorAll("td")[8]).toHaveTextContent(/^—$/); // null CPU is a dash, not 0% (column 9: after the registry columns)
    expect(within(first).getByText("LIVE")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("SYNTHETIC")).toBeInTheDocument();
  });

  it("explains an empty result with a way to clear the filters", async () => {
    renderApp(<HostsPage />, { api: fakeApi({ hosts: async () => hostList([], { total: 0 }) }), route: "/hosts?health=critical" });
    expect(await screen.findByText("No hosts match")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
  });
});

const topology = {
  environments: [{ id: "env-prod", name: "prod", regions: [{ id: "reg-eu", name: "eu-west", clusters: [{ id: "cl-a", name: "alpha", hosts: [] }] }] }],
  unassigned_regions: [],
  unassigned_clusters: [{ id: "cl-b", name: "beta", hosts: [] }],
  ungrouped_hosts: [],
  services: [{ id: "svc-api", name: "api", description: null, hosts: [] }],
  unassigned_services: [],
};

function placedApi(calls: HostListParams[], inventory: () => Promise<unknown>) {
  const placed = host({
    host_id: "web-1", hostname: "web-1",
    environment: { id: "env-prod", name: "prod" }, region: { id: "reg-eu", name: "eu-west" },
    cluster: { id: "cl-a", name: "alpha" }, services: [{ id: "svc-api", name: "api" }],
  });
  return fakeApi({
    hosts: async (p = {}) => {
      calls.push(p);
      return hostList([placed], { total: 1 });
    },
    infrastructure: inventory as never,
  });
}

describe("Hosts board: registry groups and return links", () => {
  it("shows environment, region, cluster and services from the registry, linking to Infrastructure", async () => {
    renderApp(<HostsPage />, { api: placedApi([], async () => topology), route: "/hosts" });
    const row = (await screen.findByRole("link", { name: "web-1" })).closest("tr")!;
    expect(row).toHaveTextContent("prod · eu-west");
    expect(within(row).getByRole("link", { name: "alpha" })).toHaveAttribute("href", "/infrastructure");
    expect(within(row).getByRole("link", { name: "api" })).toHaveAttribute("href", "/infrastructure");
  });

  it("filters by a group on the whole fleet: the request carries the group id and resets to page 1", async () => {
    const calls: HostListParams[] = [];
    renderApp(<HostsPage />, { api: placedApi(calls, async () => topology), route: "/hosts?page=2" });
    await screen.findByRole("link", { name: "web-1" });
    await userEvent.click(await screen.findByRole("checkbox", { name: "alpha" }));
    await waitFor(() => expect(calls.at(-1)).toMatchObject({ cluster: ["cl-a"], page: 1 }));
    await userEvent.click(screen.getByRole("checkbox", { name: "api" }));
    await waitFor(() => expect(calls.at(-1)).toMatchObject({ cluster: ["cl-a"], service: ["svc-api"] }));
  });

  it("lists groups only from the registry and never as an empty choice when the inventory fails", async () => {
    renderApp(<HostsPage />, { api: placedApi([], async () => { throw new Error("inventory down"); }), route: "/hosts" });
    // each group facet reports the failure itself: Environment, Region, Cluster and Service are all unavailable
    expect(await screen.findAllByText(/Unavailable: the infrastructure list did not load/)).toHaveLength(4);
    expect(screen.queryByText("None registered yet.")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "web-1" })).toBeInTheDocument();
  });

  it("gives each host link a return path to this filtered list, so the detail page's All hosts link keeps the filters", async () => {
    renderApp(<HostsPage />, { api: placedApi([], async () => topology), route: "/hosts?health=critical&cluster=cl-a" });
    const link = await screen.findByRole("link", { name: "web-1" });
    const href = new URL(link.getAttribute("href")!, "http://x");
    expect(href.pathname).toBe("/hosts/web-1");
    expect(href.searchParams.get("from")).toBe("/hosts?health=critical&cluster=cl-a");
  });
});
