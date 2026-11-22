/**
 * Typed relationships between scene entities, their compatibility rules, anchor resolution and visual styles.
 *
 * A connection exists only when something explicit says so (the inventory, or later a real integration). Nothing here
 * derives a dependency from position, containment or network reachability.
 */
import type { ComponentRegistry } from "./registry";
import type { ComponentDefinition, ConnectionPort, RelationType, Vec3 } from "./types";

export interface Evidence {
  /** Where the relationship came from. */
  kind: "inventory_containment" | "inventory_assignment" | "declared";
  /** Plain words shown to the user. */
  detail: string;
}

export interface SceneConnection {
  id: string;
  /** Scene entity ids (e.g. "svc:<id>", "host:<host_id>"). */
  source: string;
  target: string;
  type: RelationType;
  sourceAnchor: string;
  targetAnchor: string;
  direction?: "forward" | "none";
  metadata?: Readonly<Record<string, string>>;
  evidence: Evidence | null;
}

export interface RelationStyle {
  label: string;
  /** How the relationship is drawn. Containment is shown by nesting, never as a line that could read as a dependency. */
  render: "line" | "nesting";
  color: string;
  /** World-space line radius. */
  width: number;
  /** Dash and gap lengths in world units; null for solid. A pattern is the non-colour cue for each type. */
  dash: readonly [number, number] | null;
  arrow: boolean;
}

/**
 * Visual style per relationship type. Configurable: pass a partial override to `relationStyle`.
 * Amber is reserved for forecasts (design decision D-2) and is not used here.
 */
export const RELATION_STYLES: Readonly<Record<RelationType, RelationStyle>> = {
  CONTAINS: { label: "Contains", render: "nesting", color: "#2c4a63", width: 0.02, dash: null, arrow: false },
  RUNS_ON: { label: "Runs on", render: "line", color: "#4aa3ff", width: 0.035, dash: null, arrow: true },
  CONNECTS_TO: { label: "Connects to", render: "line", color: "#7f98ad", width: 0.03, dash: [0.25, 0.15], arrow: false },
  ROUTES_TO: { label: "Routes to", render: "line", color: "#62a8ff", width: 0.03, dash: [0.5, 0.15], arrow: true },
  READS_FROM: { label: "Reads from", render: "line", color: "#8fd3ff", width: 0.03, dash: [0.1, 0.1], arrow: true },
  WRITES_TO: { label: "Writes to", render: "line", color: "#5ec2ff", width: 0.035, dash: [0.4, 0.1], arrow: true },
  PUBLISHES_TO: { label: "Publishes to", render: "line", color: "#9fb6ff", width: 0.03, dash: [0.3, 0.3], arrow: true },
  CONSUMES_FROM: { label: "Consumes from", render: "line", color: "#b6c8ff", width: 0.03, dash: [0.15, 0.3], arrow: true },
  DEPENDS_ON: { label: "Depends on", render: "line", color: "#ff6a55", width: 0.03, dash: [0.6, 0.2], arrow: true },
  SENDS_TELEMETRY_TO: { label: "Sends telemetry to", render: "line", color: "#35d0b5", width: 0.025, dash: [0.08, 0.16], arrow: true },
};

export function relationStyle(type: RelationType, overrides: Partial<Record<RelationType, Partial<RelationStyle>>> = {}): RelationStyle {
  return { ...RELATION_STYLES[type], ...(overrides[type] ?? {}) };
}

export type Compatibility = { ok: true } | { ok: false; reason: string };

/** Whether `source -[type]-> target` is allowed by both components' declared roles. */
export function checkCompatibility(reg: ComponentRegistry, sourceType: string, targetType: string, type: RelationType): Compatibility {
  const src = reg.get(sourceType);
  const tgt = reg.get(targetType);
  if (!src) return { ok: false, reason: `Unknown source component ${sourceType}` };
  if (!tgt) return { ok: false, reason: `Unknown target component ${targetType}` };
  if (!src.compatible_connection_types.as_source.includes(type)) return { ok: false, reason: `${src.display_name} cannot be the source of ${type}` };
  if (!tgt.compatible_connection_types.as_target.includes(type)) return { ok: false, reason: `${tgt.display_name} cannot be the target of ${type}` };
  return { ok: true };
}

/** The ports a relationship type attaches to when the connection does not name them. */
export const DEFAULT_ANCHORS: Readonly<Record<RelationType, { source: string; target: string }>> = {
  CONTAINS: { source: "surface", target: "bottom" },
  RUNS_ON: { source: "bottom", target: "top" },
  CONNECTS_TO: { source: "right", target: "left" },
  ROUTES_TO: { source: "right", target: "left" },
  READS_FROM: { source: "right", target: "left" },
  WRITES_TO: { source: "right", target: "left" },
  PUBLISHES_TO: { source: "right", target: "left" },
  CONSUMES_FROM: { source: "left", target: "right" },
  DEPENDS_ON: { source: "right", target: "left" },
  SENDS_TELEMETRY_TO: { source: "back", target: "front" },
};

export interface ResolvedAnchor {
  name: string;
  offset: Vec3;
  normal: Vec3;
  /** model: a named node in the supplied GLB; registry: the declared port; fallback: neither existed. */
  origin: "model" | "registry" | "fallback";
}

/**
 * Resolve a named anchor on a component. Order: the loaded model's `anchor_<name>` node, then the registry port, then a
 * safe fallback (the top centre of the declared footprint), so a connection always has an end point.
 */
export function resolveAnchor(def: ComponentDefinition, name: string, modelAnchors?: ReadonlyMap<string, Vec3> | null): ResolvedAnchor {
  const port: ConnectionPort | undefined = def.connection_ports.find((p) => p.name === name);
  const fromModel = modelAnchors?.get(name);
  if (fromModel) return { name, offset: fromModel, normal: port?.normal ?? [0, 1, 0], origin: "model" };
  if (port) return { name, offset: port.offset, normal: port.normal, origin: "registry" };
  return { name: "fallback", offset: [0, def.footprint[1], 0], normal: [0, 1, 0], origin: "fallback" };
}

export interface Transform {
  position: Vec3;
  rotation: Vec3;
  scale: Vec3;
}

/** Apply scale, then XYZ Euler rotation (three.js order), then translation. Pure math so it can be tested without WebGL. */
export function applyTransform(p: Vec3, t: Transform): Vec3 {
  let x = p[0] * t.scale[0];
  let y = p[1] * t.scale[1];
  let z = p[2] * t.scale[2];
  const [rx, ry, rz] = t.rotation;
  // Matrix R = Rx * Ry * Rz, so apply Rz first.
  [x, y] = [x * Math.cos(rz) - y * Math.sin(rz), x * Math.sin(rz) + y * Math.cos(rz)];
  [x, z] = [x * Math.cos(ry) + z * Math.sin(ry), -x * Math.sin(ry) + z * Math.cos(ry)];
  [y, z] = [y * Math.cos(rx) - z * Math.sin(rx), y * Math.sin(rx) + z * Math.cos(rx)];
  return [x + t.position[0], y + t.position[1], z + t.position[2]];
}

/** World-space end points of a connection, with each end's resolution origin. */
export function resolveEndpoints(
  conn: SceneConnection,
  ends: {
    source: { def: ComponentDefinition; transform: Transform; anchors?: ReadonlyMap<string, Vec3> | null };
    target: { def: ComponentDefinition; transform: Transform; anchors?: ReadonlyMap<string, Vec3> | null };
  },
): { from: Vec3; to: Vec3; fromNormal: Vec3; toNormal: Vec3; source: ResolvedAnchor; target: ResolvedAnchor } {
  const s = resolveAnchor(ends.source.def, conn.sourceAnchor, ends.source.anchors);
  const t = resolveAnchor(ends.target.def, conn.targetAnchor, ends.target.anchors);
  const rot = (n: Vec3, tr: Transform) => applyTransform(n, { position: [0, 0, 0], rotation: tr.rotation, scale: [1, 1, 1] });
  return {
    from: applyTransform(s.offset, ends.source.transform),
    to: applyTransform(t.offset, ends.target.transform),
    fromNormal: rot(s.normal, ends.source.transform),
    toNormal: rot(t.normal, ends.target.transform),
    source: s,
    target: t,
  };
}
