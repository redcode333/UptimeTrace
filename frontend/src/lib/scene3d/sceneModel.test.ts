import { describe, expect, it } from "vitest";
import type { Alert, Host, Inventory, InventoryHost } from "../../api/types";
import { host } from "../../test/fixtures";
import { buildTopology, defaultCollapsed } from "../topology";
import { HOST_CELL } from "./layout";
import { healthOverlay, provenanceOverlay } from "./overlays";
import { buildScene, buildSceneFromTopology, isGroup, isHost, isService, type Scene } from "./sceneModel";

const ih = (host_id: string, extra: Partial<InventoryHost> = {}): InventoryHost => ({ host_id, display_name: null, kind: "live", deactivated: false, ...extra });

const inventory: Inventory = {
  environments: [{
    id: "e-prod", name: "production",
    regions: [{
      id: "r-eu", name: "eu-west-1",
      clusters: [{ id: "c-api", name: "api", hosts: [ih("api-1", { display_name: "API one" }), ih("api-2", { kind: "synthetic" }), ih("api-3", { kind: "replay" })] }],
    }],
  }],
  unassigned_regions: [{ id: "r-lab", name: "lab", clusters: [] }],
  unassigned_clusters: [{ id: "c-batch", name: "batch", hosts: [ih("batch-1")] }],
  ungrouped_hosts: [ih("loose-1"), ih("unmonitored-1")],
  unassigned_services: [{ id: "s-metrics", name: "metrics" }],
  services: [
    { id: "s-checkout", name: "checkout", description: "Payments", hosts: [ih("api-1"), ih("batch-1")] },
    { id: "s-metrics", name: "metrics", description: null, hosts: [] },
  ],
};

const monitored = new Map<string, Host>([
  ["api-1", host({ host_id: "api-1", health: { status: "healthy", category: "healthy", reasons: [] } })],
  ["api-2", host({ host_id: "api-2", source: "synthetic", health: { status: "critical", category: "degraded", reasons: [] } })],
  ["api-3", host({ host_id: "api-3", source: "replay", reporting_status: "replay", health: { status: "warning", category: "degraded", reasons: [] } })],
  ["batch-1", host({ host_id: "batch-1", health: { status: "offline", category: "offline", reasons: [] } })],
  ["loose-1", host({ host_id: "loose-1", health: { status: "stale", category: "stale", reasons: [] } })],
]);

const alert = (over: Partial<Alert> & { id: string; host_id: string }): Alert => ({
  condition_key: "k", rule: "threshold", metric: "system.cpu.utilization", labels: {}, severity: "critical", state: "open", value: 97, threshold: 95,
  opened_at: "2026-11-13T10:00:00Z", last_evaluated_at: "2026-11-13T10:00:00Z", acknowledged_at: null, acknowledged_by: null,
  resolved_at: null, resolved_by: null, resolution: null, ...over,
});

const alerts: Alert[] = [
  alert({ id: "al-1", host_id: "api-2" }),
  alert({ id: "al-2", host_id: "api-2", severity: "warning", metric: "system.memory.utilization", value: 88, threshold: 85, opened_at: "2026-11-13T09:00:00Z" }),
  alert({ id: "al-old", host_id: "api-1", state: "resolved" }),
];

const scene = () => buildScene(inventory, monitored, alerts);
const hostEntity = (s: Scene, id: string) => {
  const e = s.entities.get(`host:${id}`);
  if (!isHost(e)) throw new Error(`no host ${id}`);
  return e;
};

describe("scene generation from the inventory", () => {
  it("creates one scene entity per real inventory entity and nothing else", () => {
    const s = scene();
    const kinds = [...s.entities.values()].map((e) => `${e.kind}:${e.label}`).sort();
    expect(kinds).toEqual([
      "cluster:api", "cluster:batch", "environment:production",
      "group:Clusters without a region", "group:Hosts not in a cluster", "group:Regions without an environment",
      "host:API one", "host:api-2", "host:api-3", "host:batch-1", "host:loose-1", "host:unmonitored-1",
      "region:eu-west-1", "region:lab", "service:checkout", "service:metrics",
    ].sort());
  });

  it("maps every host instance to its actual host id while sharing one model", () => {
    const s = scene();
    const hosts = [...s.entities.values()].filter(isHost);
    expect(hosts.map((h) => h.hostId).sort()).toEqual(["api-1", "api-2", "api-3", "batch-1", "loose-1", "unmonitored-1"]);
    expect(new Set(hosts.map((h) => h.componentType))).toEqual(new Set(["physical-server"]));
    expect(s.modelUsage.get("physical-server")).toBe(6);
    expect(hostEntity(s, "api-1").kindLabel).toBe("Host");
    // Each instance keeps its own state.
    expect(hostEntity(s, "api-1").health.state).toBe("healthy");
    expect(hostEntity(s, "api-2").health.state).toBe("critical");
  });

  it("is reproducible: the same inputs give the same entities, connections and positions", () => {
    const a = scene();
    const b = scene();
    expect([...a.entities.keys()]).toEqual([...b.entities.keys()]);
    expect(a.connections).toEqual(b.connections);
    expect([...a.layout.placements]).toEqual([...b.layout.placements]);
  });
});

describe("relationships", () => {
  it("contains only explicit containment and explicit service assignments", () => {
    const s = scene();
    const types = new Set(s.connections.map((c) => c.type));
    expect(types).toEqual(new Set(["CONTAINS", "RUNS_ON"]));
    const runsOn = s.connections.filter((c) => c.type === "RUNS_ON").map((c) => `${c.source}->${c.target}`).sort();
    expect(runsOn).toEqual(["svc:s-checkout->host:api-1", "svc:s-checkout->host:batch-1"]);
  });

  it("never links two hosts, and never derives a dependency from sharing a cluster", () => {
    const s = scene();
    for (const c of s.connections) expect(c.source.startsWith("host:") && c.target.startsWith("host:")).toBe(false);
    expect(s.connections.some((c) => c.type === "DEPENDS_ON" || c.type === "CONNECTS_TO")).toBe(false);
  });

  it("records where each relationship came from, and attaches it to named ports", () => {
    const s = scene();
    const run = s.connections.find((c) => c.id === "runs_on:svc:s-checkout->host:api-1")!;
    expect(run.evidence?.kind).toBe("inventory_assignment");
    expect(run.evidence?.detail).toContain("Not a verified or discovered dependency");
    expect([run.sourceAnchor, run.targetAnchor]).toEqual(["bottom", "top"]);
    const contains = s.connections.find((c) => c.id === "contains:cluster:c-api->host:api-1")!;
    expect(contains.evidence?.kind).toBe("inventory_containment");
  });

  it("does not record containment for the virtual unassigned groups", () => {
    const s = scene();
    expect(s.connections.some((c) => c.source.startsWith("group:"))).toBe(false);
  });

  it("draws containment as nesting, so only service lines become visible links", () => {
    const s = scene();
    expect(s.links.map((l) => l.type)).toEqual(["RUNS_ON", "RUNS_ON"]);
  });
});

describe("health overlays", () => {
  it("shows each host's backend health, never a guess", () => {
    const s = scene();
    expect(hostEntity(s, "batch-1").health).toEqual(healthOverlay("offline"));
    expect(hostEntity(s, "loose-1").health.state).toBe("stale");
    expect(hostEntity(s, "loose-1").health.dim).toBe(true);
  });

  it("shows a host with no loaded monitoring record as 'no data loaded', not healthy", () => {
    const e = hostEntity(scene(), "unmonitored-1");
    expect(e.health.state).toBe("unloaded");
    expect(e.health.label).toBe("NO DATA LOADED");
    expect(e.alert).toBeNull();
  });

  it("gives every state a text label and a glyph, so colour is never the only cue", () => {
    for (const st of ["healthy", "warning", "critical", "stale", "offline", "unknown", "unloaded"] as const) {
      const o = healthOverlay(st);
      expect(o.label.length).toBeGreaterThan(0);
      expect(o.glyph.length).toBeGreaterThan(0);
    }
    expect(healthOverlay("critical").glyph).toBe("■");
    expect(healthOverlay("warning").glyph).toBe("▲");
    expect(healthOverlay("critical").pulse).toBe("fast");
  });
});

describe("active alert highlighting", () => {
  it("highlights exactly the affected host, with severity, rule, metric and an investigation link", () => {
    const s = scene();
    const a = hostEntity(s, "api-2").alert!;
    expect(a.severity).toBe("critical");
    expect(a.count).toBe(2);
    expect(a.items[0]).toEqual({
      id: "al-1", severity: "critical", rule: "Threshold breach", metric: "system.cpu.utilization", value: 97, threshold: 95, href: "/alerts/al-1",
    });
    expect(a.items[1]!.metric).toBe("system.memory.utilization");
    const withAlerts = [...s.entities.values()].filter(isHost).filter((h) => h.alert).map((h) => h.hostId);
    expect(withAlerts).toEqual(["api-2"]);
  });

  it("ignores resolved alerts", () => {
    expect(hostEntity(scene(), "api-1").alert).toBeNull();
  });

  it("does not mark the host's cluster, region, environment or service as failed; group figures are labelled aggregates", () => {
    const s = scene();
    const cluster = s.entities.get("cluster:c-api");
    const region = s.entities.get("region:r-eu");
    if (!isGroup(cluster) || !isGroup(region)) throw new Error("groups missing");
    expect(cluster).not.toHaveProperty("health");
    expect(cluster).not.toHaveProperty("alert");
    expect(cluster.aggregate.kind).toBe("aggregate");
    expect(cluster.aggregate.summary).toBe("Aggregate of 3 hosts: critical 1 · warning 1 · healthy 1");
    expect(cluster.aggregate.alerts).toBe(2);
    expect(region.aggregate.label).toBe("Aggregate of 3 hosts");
    const svc = s.entities.get("svc:s-checkout");
    if (!isService(svc)) throw new Error("service missing");
    expect(svc).not.toHaveProperty("health");
  });
});

describe("live, synthetic and replay stay distinguishable", () => {
  it("carries each host's provenance with a distinct label and ring pattern", () => {
    const s = scene();
    expect(hostEntity(s, "api-1").provenance).toEqual(provenanceOverlay("live"));
    expect(hostEntity(s, "api-2").provenance.label).toBe("SYNTHETIC");
    expect(hostEntity(s, "api-3").provenance.label).toBe("REPLAY");
    const patterns = new Set(["live", "synthetic", "replay"].map((k) => provenanceOverlay(k as "live").pattern));
    expect(patterns.size).toBe(3);
    expect(hostEntity(s, "api-2").provenance.isLive).toBe(false);
    expect(hostEntity(s, "api-3").provenance.isLive).toBe(false);
  });
});

describe("empty, unconfigured and unassigned", () => {
  it("produces an empty scene for an empty inventory, with no catalogue models shown", () => {
    const empty: Inventory = { environments: [], unassigned_regions: [], unassigned_clusters: [], ungrouped_hosts: [], services: [], unassigned_services: [] };
    const s = buildScene(empty, new Map(), []);
    expect(s.empty).toBe(true);
    expect(s.entities.size).toBe(0);
    expect(s.visible).toEqual([]);
    expect(s.connections).toEqual([]);
    expect(s.modelUsage.size).toBe(0);
  });

  it("keeps unassigned regions, clusters, hosts and services visible in their own groups", () => {
    const s = scene();
    for (const id of ["group:regions", "group:clusters", "group:hosts", "region:r-lab", "host:loose-1", "svc:s-metrics"]) expect(s.visible).toContain(id);
    const hostsGroup = s.entities.get("group:hosts");
    if (!isGroup(hostsGroup)) throw new Error("group missing");
    expect(hostsGroup.unassigned).toBe(true);
    expect(hostsGroup.label).toBe("Hosts not in a cluster");
  });

  it("shows a service with no hosts without inventing a link", () => {
    const s = scene();
    const svc = s.entities.get("svc:s-metrics");
    if (!isService(svc)) throw new Error("service missing");
    expect(svc.hostIds).toEqual([]);
    expect(s.connections.some((c) => c.source === "svc:s-metrics")).toBe(false);
  });
});

describe("layout", () => {
  it("places every visible entity, hosts inside their cluster's footprint", () => {
    const s = scene();
    for (const id of s.visible) expect(s.layout.placements.has(id)).toBe(true);
    const c = s.layout.placements.get("cluster:c-api")!;
    for (const h of ["api-1", "api-2", "api-3"]) {
      const p = s.layout.placements.get(`host:${h}`)!;
      expect(Math.abs(p.position[0] - c.position[0])).toBeLessThanOrEqual(c.size![0] / 2 - HOST_CELL[0] / 2 + 1e-9);
      expect(Math.abs(p.position[2] - c.position[2])).toBeLessThanOrEqual(c.size![1] / 2 - HOST_CELL[1] / 2 + 1e-9);
      expect(p.position[1]).toBeGreaterThan(c.position[1]);
    }
  });

  it("does not overlap sibling hosts", () => {
    const s = scene();
    const ps = ["api-1", "api-2", "api-3"].map((h) => s.layout.placements.get(`host:${h}`)!.position);
    for (let i = 0; i < ps.length; i++) for (let j = i + 1; j < ps.length; j++) {
      const dx = Math.abs(ps[i]![0] - ps[j]![0]);
      const dz = Math.abs(ps[i]![2] - ps[j]![2]);
      expect(dx >= HOST_CELL[0] - 1e-9 || dz >= HOST_CELL[1] - 1e-9).toBe(true);
    }
  });

  it("puts services on a deck behind the hosts", () => {
    const s = scene();
    const svc = s.layout.placements.get("svc:s-checkout")!;
    const h = s.layout.placements.get("host:api-1")!;
    expect(svc.position[1]).toBeGreaterThan(h.position[1]);
    expect(svc.position[2]).toBeLessThan(s.layout.bounds.max[2]);
  });
});

describe("large inventories", () => {
  const big: Inventory = {
    environments: [], unassigned_regions: [], ungrouped_hosts: [], unassigned_services: [],
    unassigned_clusters: [{ id: "c-big", name: "big", hosts: Array.from({ length: 80 }, (_, i) => ih(`n-${i}`)) }],
    services: [{ id: "s-web", name: "web", description: null, hosts: [ih("n-3"), ih("n-40")] }],
  };

  it("starts large hierarchies collapsed (as the 2D view does) and keeps hidden hosts as real entities", () => {
    const topo = buildTopology(big, new Map(), []);
    const collapsed = defaultCollapsed(topo);
    const s = buildSceneFromTopology(topo, { isCollapsed: (id) => collapsed.has(id) });
    expect(s.entities.size).toBe(1 + 1 + 80 + 1);
    expect(s.visible.filter((id) => id.startsWith("host:"))).toEqual([]);
    const cluster = s.entities.get("cluster:c-big");
    if (!isGroup(cluster)) throw new Error("cluster missing");
    expect(cluster.collapsed).toBe(true);
    // Lines end at the topmost collapsed container.
    expect(s.links.map((l) => l.target)).toEqual(["group:clusters"]);
    // The real relationships are unchanged by collapsing.
    expect(s.connections.filter((c) => c.type === "RUNS_ON").map((c) => c.target)).toEqual(["host:n-3", "host:n-40"]);
  });

  it("routes lines for hidden hosts to their collapsed cluster as one aggregated line", () => {
    const s = buildScene(big, new Map(), [], { isCollapsed: (id) => id === "cluster:c-big" });
    expect(s.visible).toContain("cluster:c-big");
    expect(s.links).toEqual([{ key: "RUNS_ON:svc:s-web->cluster:c-big", type: "RUNS_ON", source: "svc:s-web", target: "cluster:c-big", connectionIds: ["runs_on:svc:s-web->host:n-3", "runs_on:svc:s-web->host:n-40"], aggregated: true }]);
    expect(s.layout.placements.has("host:n-3")).toBe(false);
  });
});
