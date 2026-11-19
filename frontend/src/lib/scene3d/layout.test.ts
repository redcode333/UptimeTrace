/**
 * Layout regressions for container names. A name must sit in the band its own container reserves, never in the band of a
 * container behind or in front of it, and must stay on its plate. These run on real layouts from the topology builder.
 */
import { describe, expect, it } from "vitest";
import type { Inventory, InventoryHost } from "../../api/types";
import { buildTopology, type TopoGroup, type Topology } from "../topology";
import { GROUP_PAD, groupLabelBudget, LABEL_PAD, layoutScene, type SceneLayout } from "./layout";

const ih = (host_id: string): InventoryHost => ({ host_id, display_name: null, kind: "live", deactivated: false });

const inv: Inventory = {
  environments: [
    { id: "e1", name: "production", regions: [
      { id: "r1", name: "eu-west-1", clusters: [
        { id: "c1", name: "api", hosts: [ih("api-1"), ih("api-2")] },
        { id: "c2", name: "queue", hosts: [ih("mq-1")] },
      ] },
    ] },
  ],
  unassigned_regions: [],
  unassigned_clusters: [{ id: "c3", name: "batch", hosts: [ih("batch-1")] }],
  ungrouped_hosts: [ih("loose-1")],
  services: [],
  unassigned_services: [],
};

const topology: Topology = buildTopology(inv, new Map(), []);

function groupNamed(kind: TopoGroup["kind"], label: string): TopoGroup {
  const found = [...topology.nodes.values()].find((n): n is TopoGroup => n.kind === kind && n.label === label);
  if (!found) throw new Error(`no ${kind} named ${label}`);
  return found;
}

/** A container's name band in absolute world z: where its name lies, and the plate's back and front edges. */
function nameBand(layout: SceneLayout, node: TopoGroup) {
  const p = layout.placements.get(node.id)!;
  const [w, d] = p.size!;
  const b = groupLabelBudget([w, d]);
  const z = p.position[2];
  return {
    from: z + b.centreZ - b.height / 2,
    to: z + b.centreZ + b.height / 2,
    backEdge: z - d / 2,
    frontEdge: z + d / 2,
    budget: b,
    size: [w, d] as const,
  };
}

describe("container names", () => {
  const layout = layoutScene(topology);

  it("sits on its own plate, inside the band its back edge reserves above its children", () => {
    const containers = [...topology.nodes.values()].filter((n): n is TopoGroup => n.kind !== "host" && layout.placements.get(n.id)?.size != null);
    expect(containers.length).toBeGreaterThan(0);
    for (const node of containers) {
      const band = nameBand(layout, node);
      const [w] = band.size;
      expect(band.budget.left, `${node.label} left`).toBeGreaterThanOrEqual(-w / 2);
      expect(band.budget.left + band.budget.width, `${node.label} right`).toBeLessThanOrEqual(w / 2);
      expect(band.from, `${node.label} starts on its plate`).toBeGreaterThanOrEqual(band.backEdge);
      expect(band.to, `${node.label} ends above its children`).toBeLessThanOrEqual(band.backEdge + GROUP_PAD + LABEL_PAD);
    }
  });

  it("never shares a band with the container it is nested in", () => {
    const env = groupNamed("environment", "production");
    const region = groupNamed("region", "eu-west-1");
    const cluster = groupNamed("cluster", "api");
    const [outer, middle, inner] = [env, region, cluster].map((n) => nameBand(layout, n));
    expect(outer!.to, "environment name ends before region name starts").toBeLessThan(middle!.from);
    expect(middle!.to, "region name ends before cluster name starts").toBeLessThan(inner!.from);
  });

  it("keeps a collapsed container's name on its plate", () => {
    const api = groupNamed("cluster", "api");
    const collapsed = layoutScene(topology, (id) => id === api.id);
    const band = nameBand(collapsed, api);
    expect(band.budget.left).toBeGreaterThanOrEqual(-band.size[0] / 2);
    expect(band.from).toBeGreaterThanOrEqual(band.backEdge);
    expect(band.to).toBeLessThanOrEqual(band.frontEdge);
  });
});
