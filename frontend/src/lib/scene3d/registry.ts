/**
 * Central component registry. All lookups go through here: rendering code never hardcodes a model path or a port.
 */
import { CATALOGUE, ENTITY_BINDING } from "./catalogue";
import { CATEGORIES, RELATION_TYPES, type Category, type ComponentDefinition, type EntityKind } from "./types";

export interface ComponentRegistry {
  all(): readonly ComponentDefinition[];
  get(componentType: string): ComponentDefinition | undefined;
  /** Like get, but throws: for code paths where a missing definition is a programming error. */
  require(componentType: string): ComponentDefinition;
  getById(componentId: string): ComponentDefinition | undefined;
  byCategory(category: Category): readonly ComponentDefinition[];
  /** The model bound to a real inventory entity kind. */
  forEntity(kind: EntityKind): ComponentDefinition;
  /** Absolute URL of a component's model under the app's base path (Vite's BASE_URL). */
  assetUrl(componentType: string, base?: string): string;
}

const SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;

/** Every structural problem in a set of definitions. An empty list means the registry is valid. */
export function validateDefinitions(defs: readonly ComponentDefinition[], binding: Record<string, string> = ENTITY_BINDING): string[] {
  const errors: string[] = [];
  const types = new Set<string>();
  const ids = new Set<string>();
  for (const d of defs) {
    const where = `${d.component_type || "(no type)"}`;
    if (!SLUG.test(d.component_type)) errors.push(`${where}: component_type must be a lowercase slug`);
    if (types.has(d.component_type)) errors.push(`${where}: duplicate component_type`);
    if (ids.has(d.component_id)) errors.push(`${where}: duplicate component_id ${d.component_id}`);
    types.add(d.component_type);
    ids.add(d.component_id);
    if (!(CATEGORIES as readonly string[]).includes(d.category)) errors.push(`${where}: unknown category ${d.category}`);
    if (d.component_id !== `${d.category}.${d.component_type}`) errors.push(`${where}: component_id must be <category>.<component_type>`);
    if (!d.asset_path.startsWith("/assets/infrastructure/") || !d.asset_path.endsWith(".glb")) errors.push(`${where}: asset_path must be /assets/infrastructure/…/*.glb`);
    const portNames = d.connection_ports.map((p) => p.name);
    if (new Set(portNames).size !== portNames.length) errors.push(`${where}: duplicate connection port name`);
    for (const r of [...d.compatible_connection_types.as_source, ...d.compatible_connection_types.as_target]) {
      if (!(RELATION_TYPES as readonly string[]).includes(r)) errors.push(`${where}: unknown relation ${r}`);
    }
    if (d.default_scale.some((s) => !(s > 0))) errors.push(`${where}: default_scale must be positive`);
  }
  for (const [kind, type] of Object.entries(binding)) {
    const def = defs.find((d) => d.component_type === type);
    if (!def) errors.push(`binding ${kind}: no component ${type}`);
    else if (def.availability !== "active") errors.push(`binding ${kind}: ${type} must be marked active`);
  }
  for (const d of defs) {
    if (d.availability === "active" && !Object.values(binding).includes(d.component_type)) errors.push(`${d.component_type}: active but not bound to an entity kind`);
  }
  return errors;
}

export function createRegistry(defs: readonly ComponentDefinition[] = CATALOGUE, binding: Record<EntityKind, string> = ENTITY_BINDING): ComponentRegistry {
  const errors = validateDefinitions(defs, binding);
  if (errors.length > 0) throw new Error(`Invalid component registry:\n${errors.join("\n")}`);
  const byType = new Map(defs.map((d) => [d.component_type, d]));
  const byId = new Map(defs.map((d) => [d.component_id, d]));
  const byCat = new Map<Category, ComponentDefinition[]>();
  for (const d of defs) byCat.set(d.category, [...(byCat.get(d.category) ?? []), d]);

  const require = (t: string) => {
    const d = byType.get(t);
    if (!d) throw new Error(`Unknown component type: ${t}`);
    return d;
  };
  return {
    all: () => defs,
    get: (t) => byType.get(t),
    require,
    getById: (id) => byId.get(id),
    byCategory: (c) => byCat.get(c) ?? [],
    forEntity: (kind) => require(binding[kind]),
    assetUrl: (t, base = "/") => joinBase(base, require(t).asset_path),
  };
}

/** Join an app base path ("/", "/console/", "https://cdn.example/app/") with an absolute asset path. */
export function joinBase(base: string, path: string): string {
  return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

/** The registry used by the app. Built once; validation runs at module load and fails loudly in development. */
export const registry: ComponentRegistry = createRegistry();
