/**
 * The scene: the component registry combined with the customer's actual inventory, monitoring and alerts.
 *
 * Every scene entity is a real inventory entity and keeps its own id. Models are chosen by ENTITY_BINDING, so many hosts
 * share one model while each instance carries its own host id, health and alerts. Nothing is added from the catalogue
 * alone, and the only relationships are the ones saved in the inventory.
 */
import type { Inventory } from "../../api/types";
import { anchorFor, buildTopology, isContainer, type Topology, type TopoGroup, type TopoHost, type TopoNode, type TopoService } from "../topology";
import { checkCompatibility, DEFAULT_ANCHORS, type SceneConnection } from "./connections";
import { layoutScene, type SceneLayout } from "./layout";
import { alertOverlay, groupAggregate, healthOverlay, provenanceOverlay, type AlertOverlay, type GroupAggregate, type HealthOverlay, type ProvenanceOverlay } from "./overlays";
import { registry as defaultRegistry, type ComponentRegistry } from "./registry";
import type { EntityKind, RelationType } from "./types";

interface EntityBase {
  /** Scene id, identical to the topology node id ("host:<host_id>", "cluster:<id>", "svc:<id>", …). */
  id: string;
  kind: EntityKind;
  /** The model this entity is drawn with. */
  componentType: string;
  label: string;
  /** Kind label shown to people. Hosts say "Host", not the model's name. */
  kindLabel: string;
  parentId: string | null;
}

export interface SceneHost extends EntityBase {
  kind: "host";
  /** The real host id from the inventory and the monitoring API. */
  hostId: string;
  health: HealthOverlay;
  alert: AlertOverlay | null;
  provenance: ProvenanceOverlay;
  deactivated: boolean;
  topo: TopoHost;
}

export interface SceneGroup extends EntityBase {
  kind: "environment" | "region" | "cluster" | "group";
  /** Explicitly an aggregate of the hosts underneath; a group has no health status of its own. */
  aggregate: GroupAggregate;
  collapsed: boolean;
  /** True for the virtual groups that hold unassigned items ("Hosts not in a cluster" and similar). */
  unassigned: boolean;
  childIds: string[];
  topo: TopoGroup;
}

export interface SceneService extends EntityBase {
  kind: "service";
  serviceId: string;
  hostIds: string[];
  aggregate: GroupAggregate;
  topo: TopoService;
}

export type SceneEntity = SceneHost | SceneGroup | SceneService;

/** A relationship as drawn: real connections whose hidden ends are redirected to the collapsed container showing them. */
export interface VisibleLink {
  key: string;
  type: RelationType;
  source: string;
  target: string;
  /** The real connections this line stands for. */
  connectionIds: string[];
  /** True when at least one end is a collapsed container standing in for hidden hosts. */
  aggregated: boolean;
}

export interface Scene {
  topology: Topology;
  /** Every real entity, including hosts hidden inside collapsed containers. */
  entities: Map<string, SceneEntity>;
  /** Entities currently drawn, in a stable order. */
  visible: string[];
  /** Every real relationship, validated against the registry. */
  connections: SceneConnection[];
  links: VisibleLink[];
  layout: SceneLayout;
  /** Component types in use, with how many entities share each model. */
  modelUsage: Map<string, number>;
  empty: boolean;
}

const KIND_LABEL: Record<EntityKind, string> = {
  environment: "Environment",
  region: "Region",
  cluster: "Cluster",
  host: "Host",
  service: "Service",
  group: "Unassigned group",
};

export interface BuildOptions {
  registry?: ComponentRegistry;
  isCollapsed?: (id: string) => boolean;
}

/** Build the scene from an already-built topology (the same one the 2D topology view uses). */
export function buildSceneFromTopology(topology: Topology, opts: BuildOptions = {}): Scene {
  const reg = opts.registry ?? defaultRegistry;
  const isCollapsed = opts.isCollapsed ?? (() => false);
  const entities = new Map<string, SceneEntity>();
  const visible: string[] = [];
  const connections: SceneConnection[] = [];
  const modelUsage = new Map<string, number>();
  const use = (t: string) => modelUsage.set(t, (modelUsage.get(t) ?? 0) + 1);

  const addConnection = (c: Omit<SceneConnection, "sourceAnchor" | "targetAnchor"> & { sourceType: string; targetType: string }) => {
    const compat = checkCompatibility(reg, c.sourceType, c.targetType, c.type);
    if (!compat.ok) throw new Error(`Rejected ${c.type} ${c.source} -> ${c.target}: ${compat.reason}`);
    connections.push({
      id: c.id, source: c.source, target: c.target, type: c.type, direction: c.direction, evidence: c.evidence,
      sourceAnchor: DEFAULT_ANCHORS[c.type].source, targetAnchor: DEFAULT_ANCHORS[c.type].target,
    });
  };

  const walk = (n: TopoNode, parentId: string | null, hidden: boolean) => {
    if (n.kind === "host") {
      const def = reg.forEntity("host");
      entities.set(n.id, {
        id: n.id, kind: "host", componentType: def.component_type, label: n.label, kindLabel: KIND_LABEL.host, parentId,
        hostId: n.hostId, health: healthOverlay(n.state), alert: alertOverlay(n.alerts), provenance: provenanceOverlay(n.source),
        deactivated: n.deactivated, topo: n,
      });
      if (!hidden) visible.push(n.id);
      use(def.component_type);
      return;
    }
    const kind: SceneGroup["kind"] = n.kind;
    const def = reg.forEntity(kind);
    const collapsed = isContainer(n) && isCollapsed(n.id);
    entities.set(n.id, {
      id: n.id, kind, componentType: def.component_type, label: n.label, kindLabel: KIND_LABEL[kind], parentId,
      aggregate: groupAggregate([n]), collapsed, unassigned: kind === "group", childIds: n.children.map((c) => c.id), topo: n,
    });
    if (!hidden) visible.push(n.id);
    use(def.component_type);
    for (const c of n.children) {
      // Containment is recorded only between real groupings. Virtual "unassigned" groups contain nothing in the inventory.
      if (kind !== "group") {
        addConnection({
          id: `contains:${n.id}->${c.id}`, source: n.id, target: c.id, type: "CONTAINS", direction: "forward",
          sourceType: def.component_type, targetType: reg.forEntity(c.kind === "host" ? "host" : c.kind).component_type,
          evidence: { kind: "inventory_containment", detail: `Saved in the inventory: ${c.label} is in ${n.label}.` },
        });
      }
      walk(c, n.id, hidden || collapsed);
    }
  };
  topology.roots.forEach((r) => walk(r, null, false));

  const svcDef = reg.forEntity("service");
  const hostDef = reg.forEntity("host");
  for (const s of topology.services) {
    entities.set(s.nodeId, {
      id: s.nodeId, kind: "service", componentType: svcDef.component_type, label: s.name, kindLabel: KIND_LABEL.service, parentId: null,
      serviceId: s.id, hostIds: s.hosts.map((h) => h.hostId), aggregate: groupAggregate(s.hosts), topo: s,
    });
    visible.push(s.nodeId);
    use(svcDef.component_type);
    for (const h of s.hosts) {
      addConnection({
        id: `runs_on:${s.nodeId}->${h.id}`, source: s.nodeId, target: h.id, type: "RUNS_ON", direction: "forward",
        sourceType: svcDef.component_type, targetType: hostDef.component_type,
        evidence: { kind: "inventory_assignment", detail: `Assigned in Infrastructure: ${s.name} runs on ${h.label}. Not a verified or discovered dependency.` },
      });
    }
  }

  // Lines as drawn. Containment is shown by nesting, so only line-rendered types become links.
  const byKey = new Map<string, VisibleLink>();
  for (const c of connections) {
    if (c.type === "CONTAINS") continue;
    const source = anchorFor(topology, c.source, (id) => isCollapsed(id) && isContainer(topology.nodes.get(id)));
    const target = anchorFor(topology, c.target, (id) => isCollapsed(id) && isContainer(topology.nodes.get(id)));
    const key = `${c.type}:${source}->${target}`;
    const existing = byKey.get(key);
    if (existing) existing.connectionIds.push(c.id);
    else byKey.set(key, { key, type: c.type, source, target, connectionIds: [c.id], aggregated: source !== c.source || target !== c.target });
  }

  return {
    topology,
    entities,
    visible,
    connections,
    links: [...byKey.values()],
    layout: layoutScene(topology, (id) => isCollapsed(id) && isContainer(topology.nodes.get(id))),
    modelUsage,
    empty: topology.roots.length === 0 && topology.services.length === 0,
  };
}

/** Convenience: inventory + loaded monitoring records + active alerts → scene. */
export function buildScene(
  inv: Inventory,
  hosts: Parameters<typeof buildTopology>[1],
  alerts: Parameters<typeof buildTopology>[2],
  opts: BuildOptions = {},
): Scene {
  return buildSceneFromTopology(buildTopology(inv, hosts, alerts), opts);
}

export const isHost = (e: SceneEntity | undefined): e is SceneHost => e?.kind === "host";
export const isGroup = (e: SceneEntity | undefined): e is SceneGroup =>
  !!e && (e.kind === "environment" || e.kind === "region" || e.kind === "cluster" || e.kind === "group");
export const isService = (e: SceneEntity | undefined): e is SceneService => e?.kind === "service";
