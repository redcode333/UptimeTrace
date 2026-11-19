import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError, type ApiClient } from "../../api/client";
import type { Inventory, InventoryHost } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { InfrastructurePage } from "./InfrastructurePage";

const h = (host_id: string, extra: Partial<InventoryHost> = {}): InventoryHost => ({
  host_id, display_name: null, kind: "live", deactivated: false, ...extra,
});

function emptyInventory(): Inventory {
  return { environments: [], unassigned_regions: [], unassigned_clusters: [], ungrouped_hosts: [], services: [], unassigned_services: [] };
}

/** Every cluster in the inventory, wherever it sits (inside an environment, an unassigned region, or on its own). */
function allClusters(inv: Inventory) {
  const out: { id: string; name: string; hosts: InventoryHost[] }[] = [...inv.unassigned_clusters];
  const fromRegion = (r: { clusters: { id: string; name: string; hosts: InventoryHost[] }[] }) => out.push(...r.clusters);
  inv.environments.forEach((e) => e.regions.forEach(fromRegion));
  inv.unassigned_regions.forEach(fromRegion);
  return out;
}

/**
 * A stateful fake: each write changes the inventory that the next read returns, so assertions check what the page shows.
 * `fail` makes the next write return a server error instead.
 */
function statefulApi(initial: Inventory, fail?: ApiError) {
  let inv = structuredClone(initial);
  let failNext = fail;
  let seq = 0;
  const next = () => `id-${++seq}`;
  const writes: string[] = [];
  const guard = () => { if (failNext) { const e = failNext; failNext = undefined; throw e; } };
  const api = fakeApi({
    infrastructure: async () => structuredClone(inv),
    createEnvironment: async (name: string) => {
      guard(); writes.push(`createEnvironment:${name}`);
      const id = next(); inv.environments.push({ id, name, regions: [] }); return { id };
    },
    deleteEnvironment: async (id: string) => {
      guard(); writes.push(`deleteEnvironment:${id}`);
      const env = inv.environments.find((e) => e.id === id)!;
      // The server detaches regions rather than deleting them.
      inv.unassigned_regions.push(...env.regions);
      inv.environments = inv.environments.filter((e) => e.id !== id);
      return {};
    },
    createCluster: async (body: { name: string; region_id: string | null }) => {
      guard(); writes.push(`createCluster:${body.name}`);
      const id = next(); inv.unassigned_clusters.push({ id, name: body.name, hosts: [] }); return { id };
    },
    assignClusterHost: async (clusterId: string, hostId: string) => {
      guard(); writes.push(`assignHost:${clusterId}:${hostId}`);
      const host = inv.ungrouped_hosts.find((x) => x.host_id === hostId)!;
      inv.ungrouped_hosts = inv.ungrouped_hosts.filter((x) => x.host_id !== hostId);
      const cluster = allClusters(inv).find((c) => c.id === clusterId)!;
      cluster.hosts.push(host);
      return {};
    },
    removeClusterHost: async (clusterId: string, hostId: string) => {
      guard(); writes.push(`removeHost:${clusterId}:${hostId}`);
      const cluster = allClusters(inv).find((c) => c.id === clusterId)!;
      const host = cluster.hosts.find((x) => x.host_id === hostId)!;
      cluster.hosts = cluster.hosts.filter((x) => x.host_id !== hostId);
      inv.ungrouped_hosts.push(host);
      return {};
    },
    deleteCluster: async (id: string) => {
      guard(); writes.push(`deleteCluster:${id}`);
      const c = allClusters(inv).find((x) => x.id === id)!;
      inv.ungrouped_hosts.push(...c.hosts);
      inv.unassigned_clusters = inv.unassigned_clusters.filter((x) => x.id !== id);
      return {};
    },
  } as Partial<ApiClient>);
  return { api, writes, get inventory() { return inv; } };
}

const populated = (): Inventory => ({
  environments: [{
    id: "e1", name: "production",
    regions: [{ id: "r1", name: "eu-west-1", clusters: [{ id: "c1", name: "api", hosts: [h("api-1", { display_name: "API one" })] }] }],
  }],
  unassigned_regions: [],
  unassigned_clusters: [],
  ungrouped_hosts: [h("loose-1"), h("retired-1", { deactivated: true })],
  services: [{ id: "s1", name: "checkout", description: "Payments", hosts: [h("api-1", { display_name: "API one" })] }],
  unassigned_services: [],
});

describe("InfrastructurePage rendering", () => {
  it("shows grouped hosts inside their environment, region and cluster", async () => {
    const { api } = statefulApi(populated());
    renderApp(<InfrastructurePage />, { route: "/infrastructure?view=manage", api });
    const env = await screen.findByLabelText("Environment production");
    const region = within(env).getByLabelText("Region eu-west-1");
    const cluster = within(region).getByLabelText("Cluster api");
    expect(within(cluster).getByText(/API one \(api-1\)/)).toBeInTheDocument();
  });

  it("lists ungrouped hosts separately, marks deactivated ones, and offers no assignment for them", async () => {
    const { api } = statefulApi(populated());
    renderApp(<InfrastructurePage />, { route: "/infrastructure?view=manage", api });
    await screen.findByLabelText("Environment production");
    const section = screen.getByLabelText("Hosts not in a cluster");
    expect(within(section).getByText("loose-1")).toBeInTheDocument();
    expect(within(section).getByText(/deactivated/)).toBeInTheDocument();
    expect(within(section).queryByLabelText("Cluster for retired-1")).toBeNull();
  });

  it("renders a service spanning hosts and says where it runs", async () => {
    const { api } = statefulApi(populated());
    renderApp(<InfrastructurePage />, { route: "/infrastructure?view=manage", api });
    const svc = await screen.findByLabelText("Service checkout");
    expect(within(svc).getByText("Payments")).toBeInTheDocument();
    expect(within(svc).getByText(/API one/)).toBeInTheDocument();
  });

  it("shows an empty state that links to onboarding when nothing exists yet", async () => {
    renderApp(<InfrastructurePage />, { route: "/infrastructure?view=manage", api: statefulApi(emptyInventory()).api });
    expect(await screen.findByText("Nothing to organise yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Register a host" })).toHaveAttribute("href", "/onboarding");
  });

  it("shows an error state with retry when the inventory cannot be loaded", async () => {
    const api = fakeApi({ infrastructure: async () => { throw new ApiError(503, "unavailable", "x"); } } as Partial<ApiClient>);
    renderApp(<InfrastructurePage />, { route: "/infrastructure?view=manage", api });
    expect(await screen.findByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});

describe("InfrastructurePage changes", () => {
  it("creates an environment and shows it after the server confirms", async () => {
    const fake = statefulApi(emptyInventory());
    renderApp(<InfrastructurePage />, { route: "/infrastructure?view=manage", api: fake.api });
    await screen.findByText("Nothing to organise yet");
    fireEvent.change(screen.getByLabelText("environment name"), { target: { value: "staging" } });
    fireEvent.click(within(screen.getByLabelText("Create an environment")).getByRole("button", { name: "Add" }));
    expect(await screen.findByLabelText("Environment staging")).toBeInTheDocument();
    expect(fake.writes).toEqual(["createEnvironment:staging"]);
    expect(screen.getByRole("status")).toHaveTextContent("Environment created.");
  });

  it("blocks an invalid name before any request is sent", async () => {
    const fake = statefulApi(emptyInventory());
    renderApp(<InfrastructurePage />, { route: "/infrastructure?view=manage", api: fake.api });
    await screen.findByText("Nothing to organise yet");
    fireEvent.change(screen.getByLabelText("environment name"), { target: { value: "   " } });
    expect(within(screen.getByLabelText("Create an environment")).getByRole("button", { name: "Add" })).toBeDisabled();
    expect(fake.writes).toEqual([]);
  });

  it("assigns an ungrouped host to a cluster through the select", async () => {
    const fake = statefulApi(populated());
    renderApp(<InfrastructurePage />, { route: "/infrastructure?view=manage", api: fake.api });
    await screen.findByLabelText("Environment production");
    const section = screen.getByLabelText("Hosts not in a cluster");
    const create = within(screen.getByLabelText("Create a cluster")).getByRole("button", { name: "Add" });
    expect(create).toBeDisabled();
    fireEvent.change(within(screen.getByLabelText("Create a cluster")).getByLabelText("cluster name"), { target: { value: "batch" } });
    fireEvent.click(create);
    await screen.findByText("Cluster created.");
    const cluster = fake.inventory.unassigned_clusters[0]!;
    fireEvent.change(within(section).getByLabelText("Cluster for loose-1"), { target: { value: cluster.id } });
    fireEvent.click(within(section).getByRole("button", { name: "Add to cluster" }));
    await waitFor(() => expect(fake.writes).toContain(`assignHost:${cluster.id}:loose-1`));
    expect(await screen.findByText("Host added to the cluster.")).toBeInTheDocument();
  });

  it("removes a host from a cluster and returns it to the ungrouped list", async () => {
    const fake = statefulApi(populated());
    renderApp(<InfrastructurePage />, { route: "/infrastructure?view=manage", api: fake.api });
    const env = await screen.findByLabelText("Environment production");
    fireEvent.click(within(env).getByRole("button", { name: "Remove api-1 from api" }));
    await waitFor(() => expect(fake.writes).toEqual(["removeHost:c1:api-1"]));
    await waitFor(() => expect(within(screen.getByLabelText("Hosts not in a cluster")).getByText(/api-1/)).toBeInTheDocument());
  });

  it("asks for confirmation before deleting, and says hosts are kept", async () => {
    const fake = statefulApi(populated());
    renderApp(<InfrastructurePage />, { route: "/infrastructure?view=manage", api: fake.api });
    const env = await screen.findByLabelText("Environment production");
    fireEvent.click(within(env).getByRole("button", { name: "Delete environment production" }));
    const confirm = screen.getByRole("group", { name: "Confirm deleting environment production" });
    expect(confirm).toHaveTextContent("Its regions are kept");
    expect(fake.writes).toEqual([]);
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(fake.writes).toEqual(["deleteEnvironment:e1"]));
    expect(await screen.findByLabelText("Region eu-west-1")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Environment deleted. Its regions are kept.");
  });

  it("cancelling a delete sends nothing", async () => {
    const fake = statefulApi(populated());
    renderApp(<InfrastructurePage />, { route: "/infrastructure?view=manage", api: fake.api });
    const env = await screen.findByLabelText("Environment production");
    fireEvent.click(within(env).getByRole("button", { name: "Delete environment production" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(fake.writes).toEqual([]);
  });

  it("shows a refused change as plain words and leaves the page as it was", async () => {
    const fake = statefulApi(populated(), new ApiError(409, "host_in_other_cluster", "x"));
    renderApp(<InfrastructurePage />, { route: "/infrastructure?view=manage", api: fake.api });
    await screen.findByLabelText("Environment production");
    const section = screen.getByLabelText("Hosts not in a cluster");
    fireEvent.change(within(section).getByLabelText("Cluster for loose-1"), { target: { value: "c1" } });
    fireEvent.click(within(section).getByRole("button", { name: "Add to cluster" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert")).toHaveTextContent("That host already belongs to another cluster.");
    expect(screen.getByRole("alert")).toHaveTextContent("Nothing was changed.");
  });
});
