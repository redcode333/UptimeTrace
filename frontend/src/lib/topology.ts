/**
 * Topology model for the infrastructure map. Pure functions only.
 *
 * Containment (Environment > Region > Cluster > Host) comes from the inventory. Service membership is the explicit
 * service-to-host link saved in the inventory. Health and alerts come from the backend's own host state and alert list.
 * Nothing here infers a dependency from co-location, and nothing here evaluates health: a host without loaded monitoring
 * data is "unloaded", never "healthy".
 */
import type { Alert, Host, HealthStatus, Inventory, Source } from "../api/types";

export type TopoState = HealthStatus | "unloaded";
export type Tally = Record<TopoState, number>;

export const STATE_ORDER: readonly TopoState[] = ["critical", "warning", "stale", "offline", "unknown", "healthy", "unloaded"];
export const LARGE_HIERARCHY_HOSTS = 60;

export const emptyTally = (): Tally => ({ healthy: 0, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0, unloaded: 0 });

export interface TopoHost {
  kind: "host";
  id: string;
  hostId: string;
  label: string;
  source: Source;
  deactivated: boolean;
  state: TopoState;
  reportingStatus: Host["reporting_status"] | null;
  /** The loaded monitoring record, or null when none was loaded. */
  monitored: Host | null;
  alerts: Alert[];
  serviceNames: string[];
}

export interface TopoGroup {
  kind: "environment" | "region" | "cluster" | "group";
  id: string;
  label: string;
  children: TopoNode[];
}

export type TopoNode = TopoGroup | TopoHost;

export interface TopoService {
  kind: "service";
  id: string;
  nodeId: string;
  name: string;
  description: string | null;
  hosts: TopoHost[];
}

export interface TopoEdge {
  serviceId: string;
  hostId: string;
}

export interface Topology {
  roots: TopoNode[];
  services: TopoService[];
  edges: TopoEdge[];
  nodes: Map<string, TopoNode>;
  parent: Map<string, string | null>;
  hostCount: number;
  groupedHostCount: number;
  loadedHostCount: number;
}

export const hostNodeId = (hostId: string) => `host:${hostId}`;
export const serviceNodeId = (id: string) => `svc:${id}`;
export const isContainer = (n: TopoNode | undefined): n is TopoGroup => !!n && n.kind !== "host" && n.children.length > 0;

/** Alerts that are not resolved, keyed by host. Only the list passed in is trusted; resolved rows are ignored defensively. */
function alertsByHost(alerts: readonly Alert[]): Map<string, Alert[]> {
  const map = new Map<string, Alert[]>();
  for (const a of alerts) {
    if (a.state === "resolved") continue;
    const list = map.get(a.host_id) ?? [];
    list.push(a);
    map.set(a.host_id, list);
  }
  return map;
}

/**
 * Build the topology from the inventory, the loaded monitoring records (by host id) and the active alerts.
 * `hosts` may be partial; any inventory host without a loaded record is "unloaded".
 */
export function buildTopology(inv: Inventory, hosts: ReadonlyMap<string, Host>, alerts: readonly Alert[]): Topology {
  const alertMap = alertsByHost(alerts);
  const serviceNamesByHost = new Map<string, string[]>();
  for (const s of inv.services) {
    for (const h of s.hosts) {
      const list = serviceNamesByHost.get(h.host_id) ?? [];
      list.push(s.name);
      serviceNamesByHost.set(h.host_id, list);
    }
  }

  const nodes = new Map<string, TopoNode>();
  const parent = new Map<string, string | null>();

  const makeHost = (h: (typeof inv.ungrouped_hosts)[number], parentId: string): TopoHost => {
    const monitored = hosts.get(h.host_id) ?? null;
    const node: TopoHost = {
      kind: "host",
      id: hostNodeId(h.host_id),
      hostId: h.host_id,
      label: h.display_name ?? h.host_id,
      source: h.kind,
      deactivated: h.deactivated,
      state: monitored ? monitored.health.status : "unloaded",
      reportingStatus: monitored?.reporting_status ?? null,
      monitored,
      alerts: alertMap.get(h.host_id) ?? [],
      serviceNames: serviceNamesByHost.get(h.host_id) ?? [],
    };
    nodes.set(node.id, node);
    parent.set(node.id, parentId);
    return node;
  };

  const makeGroup = (kind: TopoGroup["kind"], id: string, label: string, parentId: string | null, children: (self: string) => TopoNode[]): TopoGroup => {
    const g: TopoGroup = { kind, id, label, children: [] };
    nodes.set(id, g);
    parent.set(id, parentId);
    g.children = children(id);
    return g;
  };

  const makeCluster = (c: { id: string; name: string; hosts: (typeof inv.ungrouped_hosts)[number][] }, parentId: string) =>
    makeGroup("cluster", `cluster:${c.id}`, c.name, parentId, (self) => c.hosts.map((h) => makeHost(h, self)));
  const makeRegion = (r: { id: string; name: string; clusters: Parameters<typeof makeCluster>[0][] }, parentId: string | null) =>
    makeGroup("region", `region:${r.id}`, r.name, parentId, (self) => r.clusters.map((c) => makeCluster(c, self)));

  const roots: TopoNode[] = [];
  for (const e of inv.environments) {
    roots.push(makeGroup("environment", `env:${e.id}`, e.name, null, (self) => e.regions.map((r) => makeRegion(r, self))));
  }
  if (inv.unassigned_regions.length > 0) {
    roots.push(makeGroup("group", "group:regions", "Regions without an environment", null, (self) => inv.unassigned_regions.map((r) => makeRegion(r, self))));
  }
  if (inv.unassigned_clusters.length > 0) {
    roots.push(makeGroup("group", "group:clusters", "Clusters without a region", null, (self) => inv.unassigned_clusters.map((c) => makeCluster(c, self))));
  }
  if (inv.ungrouped_hosts.length > 0) {
    roots.push(makeGroup("group", "group:hosts", "Hosts not in a cluster", null, (self) => inv.ungrouped_hosts.map((h) => makeHost(h, self))));
  }

  const services: TopoService[] = inv.services.map((s) => ({
    kind: "service",
    id: s.id,
    nodeId: serviceNodeId(s.id),
    name: s.name,
    description: s.description,
    hosts: s.hosts.map((h) => nodes.get(hostNodeId(h.host_id))).filter((n): n is TopoHost => !!n && n.kind === "host"),
  }));

  // Explicit service-to-host links only. Two hosts in the same cluster are NOT linked to each other.
  const edges: TopoEdge[] = services.flatMap((s) => s.hosts.map((h) => ({ serviceId: s.id, hostId: h.hostId })));

  const allHosts = [...nodes.values()].filter((n): n is TopoHost => n.kind === "host");
  return {
    roots,
    services,
    edges,
    nodes,
    parent,
    hostCount: allHosts.length,
    groupedHostCount: allHosts.filter((h) => parent.get(h.id) !== "group:hosts").length,
    loadedHostCount: allHosts.filter((h) => h.state !== "unloaded").length,
  };
}

/** Counts of the hosts under a set of nodes, by their actual state, plus the active alerts on those hosts. */
export function tallyNodes(nodes: readonly TopoNode[]): { hosts: number; tally: Tally; alerts: number } {
  const tally = emptyTally();
  let hosts = 0;
  let alerts = 0;
  const walk = (n: TopoNode) => {
    if (n.kind === "host") {
      hosts += 1;
      tally[n.state] += 1;
      alerts += n.alerts.length;
      return;
    }
    n.children.forEach(walk);
  };
  nodes.forEach(walk);
  return { hosts, tally, alerts };
}

/** Ancestors of a node from the root down to the node itself. */
export function pathOf(topo: Topology, nodeId: string): string[] {
  const path: string[] = [];
  let cur: string | null | undefined = nodeId;
  while (cur) {
    path.unshift(cur);
    cur = topo.parent.get(cur) ?? null;
  }
  return path;
}

/** Human membership of a host: Environment › Region › Cluster, omitting missing levels. */
export function membershipOf(topo: Topology, hostNode: TopoHost): string {
  const labels = pathOf(topo, hostNode.id)
    .map((id) => topo.nodes.get(id))
    .filter((n): n is TopoGroup => !!n && n.kind !== "host" && (n.kind === "environment" || n.kind === "region" || n.kind === "cluster"))
    .map((n) => n.label);
  return labels.length === 0 ? "Not in a cluster" : labels.join(" › ");
}

/**
 * Large hierarchies start with clusters and host groups collapsed so the page stays navigable. Their hosts stay reachable
 * on expansion, and their relationship lines remain visible at the collapsed row.
 */
export function defaultCollapsed(topo: Topology): Set<string> {
  if (topo.hostCount <= LARGE_HIERARCHY_HOSTS) return new Set();
  const out = new Set<string>();
  for (const [id, n] of topo.nodes) if ((n.kind === "cluster" || n.kind === "group") && n.children.length > 0) out.add(id);
  return out;
}

/**
 * The visible node that stands in for a node whose ancestors are collapsed: the topmost collapsed ancestor. A visible node
 * stands in for itself. Relationship lines end here, so a collapsed cluster still shows that its hosts run a service.
 */
export function anchorFor(topo: Topology, nodeId: string, isCollapsed: (id: string) => boolean): string {
  const path = pathOf(topo, nodeId);
  for (const id of path.slice(0, -1)) {
    if (isContainer(topo.nodes.get(id)) && isCollapsed(id)) return id;
  }
  return nodeId;
}

export interface VisibleEdge {
  key: string;
  serviceNodeId: string;
  targetNodeId: string;
  hostIds: string[];
}

/** Service lines as they appear on screen. Several hosts behind one collapsed container become one line. */
export function visibleEdges(topo: Topology, isCollapsed: (id: string) => boolean): VisibleEdge[] {
  const byKey = new Map<string, VisibleEdge>();
  for (const e of topo.edges) {
    const svc = serviceNodeId(e.serviceId);
    const target = anchorFor(topo, hostNodeId(e.hostId), isCollapsed);
    const key = `${svc}->${target}`;
    const existing = byKey.get(key);
    if (existing) existing.hostIds.push(e.hostId);
    else byKey.set(key, { key, serviceNodeId: svc, targetNodeId: target, hostIds: [e.hostId] });
  }
  return [...byKey.values()];
}

export const RULE_TEXT: Record<Alert["rule"], string> = { threshold: "Threshold breach", host_offline: "Host offline" };

/** Highest severity among a host's active alerts, for the indicator. */
export function topSeverity(alerts: readonly Alert[]): Alert["severity"] | null {
  if (alerts.some((a) => a.severity === "critical")) return "critical";
  if (alerts.some((a) => a.severity === "warning")) return "warning";
  return null;
}
