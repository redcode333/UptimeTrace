/**
 * The asset contract for the 3D infrastructure scene.
 *
 * Three things are kept apart on purpose:
 *  - the component registry (this file's ComponentDefinition): which models exist and how to place and connect them;
 *  - the infrastructure inventory (api/types Inventory): what the customer actually configured;
 *  - the scene (sceneModel.ts): the two combined. A model appears in the scene only when a real entity is bound to it.
 */

export type Vec3 = readonly [number, number, number];

export const CATEGORIES = [
  "scope",
  "compute",
  "networking",
  "data",
  "application",
  "messaging",
  "observability",
  "cloud",
] as const;
export type Category = (typeof CATEGORIES)[number];

export const CATEGORY_LABEL: Record<Category, string> = {
  scope: "Scopes and grouping",
  compute: "Compute",
  networking: "Networking",
  data: "Databases and storage",
  application: "Applications and services",
  messaging: "Messaging and streaming",
  observability: "Observability",
  cloud: "Cloud and external systems",
};

/** Typed relationships. Each one is explicit; none is inferred from position, containment or network reachability. */
export const RELATION_TYPES = [
  "CONTAINS",
  "RUNS_ON",
  "CONNECTS_TO",
  "ROUTES_TO",
  "READS_FROM",
  "WRITES_TO",
  "PUBLISHES_TO",
  "CONSUMES_FROM",
  "DEPENDS_ON",
  "SENDS_TELEMETRY_TO",
] as const;
export type RelationType = (typeof RELATION_TYPES)[number];

/** States a model can display. "unloaded" means no monitoring record was loaded: it is never shown as healthy. */
export const SCENE_STATES = ["healthy", "warning", "critical", "stale", "offline", "unknown", "unloaded"] as const;
export type SceneState = (typeof SCENE_STATES)[number];

/**
 * A named attachment point on a model, in the model's local space (before default scale and rotation).
 * A supplied GLB can override the offset with an empty node named `anchor_<name>`.
 */
export interface ConnectionPort {
  name: string;
  offset: Vec3;
  /** Outward direction, used to bend relationship curves away from the model. */
  normal: Vec3;
}

export interface MetadataField {
  type: "string" | "number" | "boolean";
  label: string;
}

export interface ComponentDefinition {
  /** Stable slug, unique across the registry. Also the file name of the model. */
  component_type: string;
  /** Stable dotted id: `<category>.<component_type>`. */
  component_id: string;
  display_name: string;
  category: Category;
  /** Public URL path of the GLB, relative to the app base: /assets/infrastructure/<category>/<component_type>.glb */
  asset_path: string;
  default_scale: Vec3;
  /** Euler angles in radians, XYZ order. */
  default_rotation: Vec3;
  /** Offset applied to every instance, e.g. to lift a model whose origin is not at its base. */
  default_position: Vec3;
  supported_states: readonly SceneState[];
  connection_ports: readonly ConnectionPort[];
  /** Relationship types this component may take part in, per role. */
  compatible_connection_types: { as_source: readonly RelationType[]; as_target: readonly RelationType[] };
  metadata_schema: Readonly<Record<string, MetadataField>>;
  /**
   * "active": bound to an entity type UptimeTrace has real data for today.
   * "catalogue": registered and loadable, but no customer resource of this type is shown until real data exists.
   */
  availability: "active" | "catalogue";
  /**
   * "model": drawn from the GLB at asset_path (or a development placeholder until it is supplied).
   * "layout-plate": a scope drawn as a plate sized to its contents by the layout. No model file is loaded for it.
   */
  rendering: "model" | "layout-plate";
  /** Approximate footprint in scene units (x, y, z) after default scale; used for layout and placeholder sizing. */
  footprint: Vec3;
}

/** Entity kinds UptimeTrace has real inventory data for (first implementation scope). */
export type EntityKind = "environment" | "region" | "cluster" | "host" | "service" | "group";
