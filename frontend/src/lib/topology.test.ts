import { describe, expect, it } from "vitest";
import type { Alert, Host, Inventory, InventoryHost } from "../api/types";
import { host } from "../test/fixtures";
import {
  anchorFor, buildTopology, defaultCollapsed, LARGE_HIERARCHY_HOSTS, membershipOf, tallyNodes, topSeverity, visibleEdges,
  type TopoHost, type Topology,
} from "./topology";

const ih = (host_id: string, extra: Partial<InventoryHost> = {}): InventoryHost => ({ host_id, display_name: null, kind: "live", deactivated: false, ...extra });

function alert(over: Partial<Alert> & { id: string; host_id: string }): Alert {
  return {
    condition_key: "k", rule: "threshold", metric: "system.cpu.utilization", labels: {}, severity: "critical", state: "open",
    value: 97, threshold: 95, opened_at: "2026-11-13T10:00:00Z", last_evaluated_at: "2026-11-13T10:00:00Z",
    acknowledged_at: null, acknowledged_by: null, resolved_at: null, resolved_by: null, resolution: null, ...over,
  };
}

/** production > eu-west-1 > api (api-1 healthy, api-2 synthetic critical); us-east-1 empty; staging > stg-1; batch (no region) */
const inv: Inventory = {
  environments: [
    {
      id: "e-prod", name: "production",
      regions: [
        { id: "r-eu", name: "eu-west-1", clusters: [{ id: "c-api", name: "api", hosts: [ih("api-1", { display_name: "API one" }), ih("api-2", { kind: "synthetic" })] }] },
        { id: "r-us", name: "us-east-1", clusters: [] },
      ],
    },
    { id: "e-stg", name: "staging", regions: [{ id: "r-stg", name: "stg-1", clusters: [] }] },
  ],
  unassigned_regions: [],
  unassigned_clusters: [{ id: "c-batch", name: "batch", hosts: [ih("batch-1")] }],
  ungrouped_hosts: [ih("loose-1"), ih("retired-1", { deactivated: true })],
  services: [
    { id: "s-checkout", name: "checkout", description: "Payments", hosts: [ih("api-1"), ih("batch-1")] },
    { id: "s-worker", name: "worker", description: null, hosts: [ih("api-1"), ih("loose-1")] },
    { id: "s-metrics", name: "metrics", description: null, hosts: [] },
  ],
  unassigned_services: [{ id: "s-metrics", name: "metrics" }],
};

const monitored = new Map<string, Host>([
  ["api-1", host({ host_id: "api-1", health: { status: "healthy", category: "healthy", reasons: [] } })],
  ["api-2", host({ host_id: "api-2", source: "synthetic", health: { status: "critical", category: "degraded", reasons: [] } })],
  ["loose-1", host({ host_id: "loose-1", health: { status: "stale", category: "stale", reasons: [] } })],
]);

const alerts = [alert({ id: "a1", host_id: "api-2" }), alert({ id: "a2", host_id: "api-2", severity: "warning", metric: "system.memory.utilization" }), alert({ id: "a3", host_id: "loose-1", state: "resolved" })];

function build(over: Partial<Inventory> = {}, mon = monitored, al = alerts): Topology {
  return buildTopology({ ...inv, ...over }, mon, al);
}

const hostNode = (t: Topology, hostId: string) => t.nodes.get(`host:${hostId}`) as TopoHost;

describe("hierarchy construction", () => {
  it("nests environment > region > cluster > host from the inventory", () => {
    const t = build();
    const env = t.nodes.get("env:e-prod")!;
    expect(env.kind).toBe("environment");
    const eu = t.nodes.get("region:r-eu")!;
    expect(t.parent.get("region:r-eu")).toBe("env:e-prod");
    expect(t.parent.get("cluster:c-api")).toBe("region:r-eu");
    expect(t.parent.get("host:api-1")).toBe("cluster:c-api");
    expect(eu.kind === "region" && eu.children.map((c) => c.label)).toEqual(["api"]);
  });

  it("keeps multiple environments, regions and clusters, including empty regions", () => {
    const t = build();
    expect(t.roots.filter((r) => r.kind === "environment").map((r) => r.label)).toEqual(["production", "staging"]);
    const prod = t.nodes.get("env:e-prod") as { children: { label: string }[] };
    expect(prod.children.map((r) => r.label)).toEqual(["eu-west-1", "us-east-1"]);
    expect((t.nodes.get("region:r-us") as { children: unknown[] }).children).toEqual([]);
  });

  it("shows unassigned clusters and ungrouped hosts as groups, never dropping them", () => {
    const t = build();
    expect(t.parent.get("cluster:c-batch")).toBe("group:clusters");
    expect(t.parent.get("host:loose-1")).toBe("group:hosts");
    expect(t.nodes.get("group:hosts")!.label).toBe("Hosts not in a cluster");
  });

  it("omits group nodes that have nothing to show", () => {
    const t = build({ unassigned_clusters: [], ungrouped_hosts: [] });
    expect(t.nodes.has("group:clusters")).toBe(false);
    expect(t.nodes.has("group:hosts")).toBe(false);
    expect(t.hostCount).toBe(2);
  });

  it("counts hosts in a cluster separately from ungrouped hosts", () => {
    const t = build();
    // api-1, api-2 and batch-1 are in clusters; loose-1 and retired-1 are not.
    expect(t.hostCount).toBe(5);
    expect(t.groupedHostCount).toBe(3);
  });
});

describe("services and relationships", () => {
  it("links a service to each host it was assigned, including hosts in different clusters", () => {
    const t = build();
    const checkout = t.edges.filter((e) => e.serviceId === "s-checkout").map((e) => e.hostId).sort();
    expect(checkout).toEqual(["api-1", "batch-1"]);
  });

  it("lets one host run several services", () => {
    const t = build();
    expect(t.edges.filter((e) => e.hostId === "api-1").map((e) => e.serviceId).sort()).toEqual(["s-checkout", "s-worker"]);
    expect(hostNode(t, "api-1").serviceNames.sort()).toEqual(["checkout", "worker"]);
  });

  it("keeps an unassigned service visible with no lines", () => {
    const t = build();
    const metrics = t.services.find((s) => s.name === "metrics")!;
    expect(metrics.hosts).toEqual([]);
    expect(t.edges.some((e) => e.serviceId === "s-metrics")).toBe(false);
  });

  it("never infers a relationship from co-location: hosts in the same cluster are not linked", () => {
    const t = build({ services: [] });
    expect(t.edges).toEqual([]);
    expect(t.parent.get("host:api-1")).toBe(t.parent.get("host:api-2"));
  });
});

describe("health and alert truth", () => {
  it("uses the backend's health status exactly, never a frontend evaluation", () => {
    const t = build();
    expect(hostNode(t, "api-2").state).toBe("critical");
    expect(hostNode(t, "loose-1").state).toBe("stale");
  });

  it("marks a host with no loaded monitoring record as unloaded, not healthy", () => {
    const t = build();
    expect(hostNode(t, "batch-1").state).toBe("unloaded");
    expect(hostNode(t, "batch-1").monitored).toBeNull();
    expect(t.loadedHostCount).toBe(3);
  });

  it("attaches active alerts only to the host they belong to", () => {
    const t = build();
    expect(hostNode(t, "api-2").alerts.map((a) => a.id)).toEqual(["a1", "a2"]);
    expect(hostNode(t, "api-1").alerts).toEqual([]);
    expect(hostNode(t, "loose-1").alerts).toEqual([]);
  });

  it("ignores resolved alerts even if they are passed in", () => {
    const t = build();
    expect(hostNode(t, "loose-1").alerts).toEqual([]);
  });

  it("picks critical over warning for the indicator", () => {
    expect(topSeverity(alerts.filter((a) => a.host_id === "api-2"))).toBe("critical");
    expect(topSeverity([])).toBeNull();
  });

  it("aggregates counts by actual state without inventing a group health", () => {
    const t = build();
    const cluster = tallyNodes([t.nodes.get("cluster:c-api")!]);
    expect(cluster.hosts).toBe(2);
    expect(cluster.tally.healthy).toBe(1);
    expect(cluster.tally.critical).toBe(1);
    expect(cluster.alerts).toBe(2);
    const region = tallyNodes([t.nodes.get("region:r-us")!]);
    expect(region.hosts).toBe(0);
  });
});

describe("containment and membership wording", () => {
  it("describes membership from the saved hierarchy", () => {
    const t = build();
    expect(membershipOf(t, hostNode(t, "api-1"))).toBe("production › eu-west-1 › api");
    expect(membershipOf(t, hostNode(t, "batch-1"))).toBe("batch");
    expect(membershipOf(t, hostNode(t, "loose-1"))).toBe("Not in a cluster");
  });
});

describe("collapsing and visible lines", () => {
  it("keeps everything open for a small hierarchy", () => {
    expect(defaultCollapsed(build()).size).toBe(0);
  });

  it("collapses clusters by default for a large hierarchy", () => {
    const many = Array.from({ length: LARGE_HIERARCHY_HOSTS + 1 }, (_, i) => ih(`bulk-${i}`));
    const t = build({ ungrouped_hosts: many, environments: [], unassigned_clusters: [], services: [] }, new Map(), []);
    expect(t.hostCount).toBe(LARGE_HIERARCHY_HOSTS + 1);
    expect([...defaultCollapsed(t)]).toEqual(["group:hosts"]);
    const clustered = build({ environments: [], unassigned_clusters: [{ id: "c-big", name: "big", hosts: many }], ungrouped_hosts: [], services: [] }, new Map(), []);
    expect([...defaultCollapsed(clustered)].sort()).toEqual(["cluster:c-big", "group:clusters"]);
  });

  it("anchors a host to its topmost collapsed container, and to itself when nothing is collapsed", () => {
    const t = build();
    expect(anchorFor(t, "host:api-1", () => false)).toBe("host:api-1");
    expect(anchorFor(t, "host:api-1", (id) => id === "cluster:c-api")).toBe("cluster:c-api");
    expect(anchorFor(t, "host:api-1", (id) => id === "env:e-prod" || id === "cluster:c-api")).toBe("env:e-prod");
  });

  it("merges several hosts behind one collapsed cluster into a single line per service", () => {
    const t = build();
    const open = visibleEdges(t, () => false);
    expect(open.map((e) => e.key).sort()).toEqual([
      "svc:s-checkout->host:api-1",
      "svc:s-checkout->host:batch-1",
      "svc:s-worker->host:api-1",
      "svc:s-worker->host:loose-1",
    ]);
    const collapsed = visibleEdges(t, (id) => id === "cluster:c-api");
    const checkoutToApi = collapsed.find((e) => e.key === "svc:s-checkout->cluster:c-api");
    expect(checkoutToApi?.hostIds).toEqual(["api-1"]);
    expect(collapsed.some((e) => e.key.endsWith("->host:api-1"))).toBe(false);
  });
});
