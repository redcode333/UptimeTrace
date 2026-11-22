import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Group } from "three";
import { describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../../api/client";
import type { Alert, Host, Inventory, InventoryHost } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { host } from "../../test/fixtures";
import { InfrastructurePage } from "./InfrastructurePage";
import type { InfraCanvasProps } from "./scene3d/InfraCanvas";

// jsdom has no WebGL. The renderer is replaced by a stub that exposes what it was asked to draw, so the view's data,
// selection and model-status wiring are tested for real. Model loading uses the real cache with a stubbed loader:
// only the physical-server model "exists".
vi.mock("./scene3d/InfraCanvas", () => ({
  default: (p: InfraCanvasProps) => (
    <div data-testid="canvas">
      {p.scene.visible.map((id) => (
        <button key={id} type="button" data-testid="drawn" data-id={id} onClick={() => p.onSelect(id)}>{id}</button>
      ))}
      <span data-testid="links">{p.scene.links.map((l) => `${l.source}->${l.target}`).join(",")}</span>
      <span data-testid="canvas-selected">{p.selected ?? ""}</span>
    </div>
  ),
}));
vi.mock("../../lib/scene3d/modelAssets", async () => {
  const actual = await vi.importActual<typeof import("../../lib/scene3d/modelAssets")>("../../lib/scene3d/modelAssets");
  const { createAssetCache } = await import("../../lib/scene3d/assetCache");
  const loader = vi.fn(async (url: string) => {
    if (url.endsWith("/compute/physical-server.glb")) return new Group();
    throw new Error("404 Not Found");
  });
  return { ...actual, modelCache: createAssetCache(loader, () => new Map()) };
});
vi.mock("./scene3d/Infrastructure3DView", async () => {
  const actual = await vi.importActual<typeof import("./scene3d/Infrastructure3DView")>("./scene3d/Infrastructure3DView");
  const View = actual.default;
  // Tests choose WebGL availability through a global so the page under test stays unmodified.
  return { ...actual, default: (p: Parameters<typeof View>[0]) => <View {...p} webgl={(globalThis as { __webgl?: boolean }).__webgl ?? false} /> };
});

const ih = (host_id: string, extra: Partial<InventoryHost> = {}): InventoryHost => ({ host_id, display_name: null, kind: "live", deactivated: false, ...extra });

const inventory: Inventory = {
  environments: [{ id: "e-prod", name: "production", regions: [{ id: "r-eu", name: "eu-west-1", clusters: [{ id: "c-api", name: "api", hosts: [ih("api-1"), ih("api-2", { kind: "synthetic" })] }] }] }],
  unassigned_regions: [],
  unassigned_clusters: [],
  ungrouped_hosts: [ih("loose-1", { kind: "replay" })],
  unassigned_services: [],
  services: [{ id: "s-checkout", name: "checkout", description: null, hosts: [ih("api-1")] }],
};
const monitored: Host[] = [
  host({ host_id: "api-1" }),
  host({ host_id: "api-2", source: "synthetic", health: { status: "critical", category: "degraded", reasons: [] } }),
];
const alerts: Alert[] = [{
  id: "al-1", host_id: "api-2", condition_key: "k1", rule: "threshold", metric: "system.cpu.utilization", labels: {}, severity: "critical", state: "open",
  value: 97, threshold: 95, opened_at: "2026-11-13T10:00:00Z", last_evaluated_at: "2026-11-13T10:00:00Z", acknowledged_at: null, acknowledged_by: null,
  resolved_at: null, resolved_by: null, resolution: null,
}];
const facetsEmpty = { source: { live: 0, synthetic: 0, replay: 0 }, health: { healthy: 0, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0 } };
const thresholds = { host_stale_after_seconds: 90, offline_after_seconds: 300, stale_after_seconds: 300, rules: {} };

function api(inv: Inventory = inventory, total = monitored.length) {
  return fakeApi({
    infrastructure: async () => inv,
    hosts: async (p?: { page?: number }) => ({ items: (p?.page ?? 1) === 1 ? monitored : [], page: p?.page ?? 1, page_size: 200, total, facets: facetsEmpty, thresholds }),
    alerts: async () => ({ alerts, total: alerts.length }),
  } as Partial<ApiClient>);
}

async function open3d(opts: { webgl: boolean; inv?: Inventory; total?: number }) {
  (globalThis as { __webgl?: boolean }).__webgl = opts.webgl;
  renderApp(<InfrastructurePage />, { api: api(opts.inv, opts.total), route: "/infrastructure?view=3d" });
}

describe("3D view tab", () => {
  it("is a third tab next to Topology and Manage, selected by ?view=3d", async () => {
    await open3d({ webgl: true });
    const tab = await screen.findByRole("tab", { name: "3D view" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Topology" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tab", { name: "Manage" })).toHaveAttribute("aria-selected", "false");
  });

  it("draws exactly the inventory's entities and its explicit service links", async () => {
    await open3d({ webgl: true });
    await screen.findByTestId("canvas");
    const drawn = screen.getAllByTestId("drawn").map((b) => b.dataset.id).sort();
    expect(drawn).toEqual(["cluster:c-api", "env:e-prod", "group:hosts", "host:api-1", "host:api-2", "host:loose-1", "region:r-eu", "svc:s-checkout"]);
    expect(screen.getByTestId("links")).toHaveTextContent(/^svc:s-checkout->host:api-1$/);
  });

  it("explains that WebGL is missing and offers the Topology tab instead of drawing anything", async () => {
    await open3d({ webgl: false });
    expect(await screen.findByText("The 3D view needs WebGL, which is not available in this browser.")).toBeInTheDocument();
    expect(screen.queryByTestId("canvas")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open the Topology tab" }));
    expect(await screen.findByRole("tab", { name: "Topology" })).toHaveAttribute("aria-selected", "true");
  });

  it("shows an empty state, not models from the library, when nothing is configured", async () => {
    const empty: Inventory = { environments: [], unassigned_regions: [], unassigned_clusters: [], ungrouped_hosts: [], services: [], unassigned_services: [] };
    await open3d({ webgl: true, inv: empty, total: 0 });
    expect(await screen.findByText("No infrastructure configured yet")).toBeInTheDocument();
    expect(screen.queryByTestId("canvas")).not.toBeInTheDocument();
  });

  it("states partial monitoring coverage with the same wording as the Topology tab", async () => {
    await open3d({ webgl: true, total: 5 });
    expect(await screen.findByText(/Monitoring data is loaded for 2 of 5 hosts/)).toBeInTheDocument();
  });
});

describe("3D view: models", () => {
  it("lists only missing model files as development placeholders, with how many objects use each", async () => {
    await open3d({ webgl: true });
    const notice = await screen.findByText("Development placeholder models.");
    const box = notice.closest(".scene3d-placeholder") as HTMLElement;
    await waitFor(() => expect(within(box).getByText("/assets/infrastructure/application/microservice.glb")).toBeInTheDocument());
    expect(box).toHaveTextContent("Microservice · used by 1 object");
    // The host model was supplied, and scopes are drawn by the layout: neither is listed.
    expect(box).not.toHaveTextContent("physical-server.glb");
    expect(box).not.toHaveTextContent("cluster.glb");
  });

  it("requests each model file once, however many objects share it, and never requests scope models", async () => {
    const { modelCache } = await import("../../lib/scene3d/modelAssets");
    await open3d({ webgl: true });
    await screen.findByText("Development placeholder models.");
    await waitFor(() => expect(modelCache.peek("/assets/infrastructure/application/microservice.glb")).toMatchObject({ status: "missing" }));
    expect(modelCache.peek("/assets/infrastructure/compute/physical-server.glb")).toMatchObject({ status: "ready" });
    // Three hosts and one service share two model files: exactly two loads, cached for every later render.
    expect(modelCache.loaderCalls()).toBe(2);
    for (const scope of ["scope/environment", "scope/cluster", "scope/unassigned-group", "networking/region"]) {
      expect(modelCache.peek(`/assets/infrastructure/${scope}.glb`)).toBeUndefined();
    }
  });
});

describe("3D view: selection and details", () => {
  it("selecting a host shows its real health, source and alert with a link to the existing investigation page", async () => {
    await open3d({ webgl: true });
    await screen.findByTestId("canvas");
    fireEvent.click(screen.getByRole("button", { name: "host:api-2" }));
    const details = await screen.findByRole("complementary", { name: "Details" });
    expect(within(details).getByText("api-2")).toBeInTheDocument();
    expect(within(details).getByText("SYNTHETIC")).toBeInTheDocument();
    expect(within(details).getByText("CRITICAL")).toBeInTheDocument();
    expect(within(details).getByRole("link", { name: "Open alert" })).toHaveAttribute("href", "/alerts/al-1");
    const model = screen.getByRole("complementary", { name: "Scene object" });
    expect(model).toHaveTextContent("Drawn with the Physical server model, shared by 3 objects. Supplied model.");
    expect(model).toHaveTextContent("Entity: host:api-2");
  });

  it("selecting a cluster shows an aggregate, never a health status of its own", async () => {
    await open3d({ webgl: true });
    await screen.findByTestId("canvas");
    fireEvent.change(screen.getByLabelText("Select in scene"), { target: { value: "cluster:c-api" } });
    expect(screen.getByTestId("canvas-selected")).toHaveTextContent("cluster:c-api");
    const model = await screen.findByRole("complementary", { name: "Scene object" });
    expect(model).toHaveTextContent("Aggregate of 2 hosts: critical 1 · healthy 1. A group has no health status of its own.");
    expect(model).toHaveTextContent("Scopes do not use a model file.");
  });

  it("explains a service link as an inventory assignment, not a dependency", async () => {
    await open3d({ webgl: true });
    await screen.findByTestId("canvas");
    fireEvent.click(screen.getByRole("button", { name: "svc:s-checkout" }));
    const model = await screen.findByRole("complementary", { name: "Scene object" });
    expect(model).toHaveTextContent("Runs on: api-1 (Assigned in Infrastructure: checkout runs on api-1. Not a verified or discovered dependency.)");
  });

  it("lets keyboard users collapse and expand a group from the details panel", async () => {
    await open3d({ webgl: true });
    await screen.findByTestId("canvas");
    fireEvent.change(screen.getByLabelText("Select in scene"), { target: { value: "cluster:c-api" } });
    fireEvent.click(await screen.findByRole("button", { name: "Collapse in scene" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "host:api-1" })).not.toBeInTheDocument());
    expect(screen.getByTestId("links")).toHaveTextContent("svc:s-checkout->cluster:c-api");
    fireEvent.click(screen.getByRole("button", { name: "Expand in scene" }));
    expect(await screen.findByRole("button", { name: "host:api-1" })).toBeInTheDocument();
  });
});
