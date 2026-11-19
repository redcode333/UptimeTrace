/**
 * DEVELOPMENT PLACEHOLDERS. Used only when a component's GLB has not been supplied or failed to load.
 *
 * They are procedural stand-ins sized from the registry footprint, drawn with a cyan "blueprint" edge so they are never
 * mistaken for finished artwork, and every placeholder is reported in the UI. Each template is built once and cloned per
 * instance through the same path as a supplied model, so the reuse pipeline is exercised even before assets exist.
 */
import {
  BoxGeometry, CylinderGeometry, EdgesGeometry, Group, IcosahedronGeometry, LineBasicMaterial, LineSegments, Mesh,
  MeshStandardMaterial, SphereGeometry, type BufferGeometry, type Material, type Object3D,
} from "three";
import type { ComponentDefinition } from "../../../lib/scene3d/types";

export const PLACEHOLDER_EDGE = "#3fd6ff";
export const LED_NAME = "status_led";

const body = new MeshStandardMaterial({ color: "#25405a", metalness: 0.55, roughness: 0.38 });
const bodyDark = new MeshStandardMaterial({ color: "#132233", metalness: 0.5, roughness: 0.5 });
const panel = new MeshStandardMaterial({ color: "#0d1926", metalness: 0.3, roughness: 0.6 });
const bay = new MeshStandardMaterial({ color: "#26405a", metalness: 0.4, roughness: 0.45 });
const coreGlow = new MeshStandardMaterial({ color: "#4aa3ff", emissive: "#4aa3ff", emissiveIntensity: 1.4, roughness: 0.3 });
const glass = new MeshStandardMaterial({ color: "#16324a", metalness: 0.1, roughness: 0.15, transparent: true, opacity: 0.55 });
/** Neutral LED: replaced per instance with the health colour by the renderer. */
export const neutralLed = new MeshStandardMaterial({ color: "#4d6378", emissive: "#4d6378", emissiveIntensity: 0.6 });
const edgeMat = new LineBasicMaterial({ color: PLACEHOLDER_EDGE, transparent: true, opacity: 0.35 });

function part(geo: BufferGeometry, mat: Material, x: number, y: number, z: number, name = ""): Mesh {
  const m = new Mesh(geo, mat);
  m.position.set(x, y, z);
  m.name = name;
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

function withEdges(m: Mesh): Mesh {
  m.add(new LineSegments(new EdgesGeometry(m.geometry, 30), edgeMat));
  return m;
}

/** Rack server: chassis, recessed front panel, drive bays, a status LED strip and top vents. */
function serverPlaceholder([w, h, d]: readonly [number, number, number]): Group {
  const g = new Group();
  g.add(withEdges(part(new BoxGeometry(w, h, d), body, 0, h / 2, 0, "chassis")));
  g.add(part(new BoxGeometry(w * 0.92, h * 0.78, 0.04), panel, 0, h / 2, d / 2 + 0.005));
  const bays = 6;
  const bayW = (w * 0.62) / bays;
  for (let i = 0; i < bays; i++) {
    g.add(part(new BoxGeometry(bayW * 0.82, h * 0.5, 0.03), bay, -w * 0.4 + bayW * (i + 0.5), h * 0.52, d / 2 + 0.03));
  }
  const led = part(new BoxGeometry(w * 0.2, h * 0.12, 0.03), neutralLed, w * 0.32, h * 0.62, d / 2 + 0.035, LED_NAME);
  led.castShadow = false;
  g.add(led);
  for (let i = 0; i < 5; i++) g.add(part(new BoxGeometry(w * 0.7, 0.012, 0.05), panel, 0, h + 0.006, -d * 0.3 + i * 0.12));
  return g;
}

/** Microservice / application: a hexagonal prism with a glowing core. */
function servicePlaceholder([w, h]: readonly [number, number, number]): Group {
  const g = new Group();
  const r = w / 2;
  g.add(withEdges(part(new CylinderGeometry(r, r, h * 0.7, 6), glass, 0, h * 0.5, 0, "shell")));
  g.add(part(new CylinderGeometry(r * 0.45, r * 0.45, h * 0.5, 6), coreGlow, 0, h * 0.5, 0, "core"));
  g.add(part(new CylinderGeometry(r * 1.05, r * 1.05, 0.06, 6), bodyDark, 0, h * 0.13, 0));
  g.add(part(new CylinderGeometry(r * 1.05, r * 1.05, 0.06, 6), bodyDark, 0, h * 0.87, 0));
  return g;
}

/** Database / storage: a stack of three discs. */
function dataPlaceholder([w, h]: readonly [number, number, number]): Group {
  const g = new Group();
  const r = w / 2;
  for (let i = 0; i < 3; i++) g.add(withEdges(part(new CylinderGeometry(r, r, h * 0.28, 32), body, 0, h * (0.16 + i * 0.33), 0)));
  const led = part(new BoxGeometry(r * 0.5, h * 0.06, 0.03), neutralLed, 0, h * 0.84, r + 0.01, LED_NAME);
  g.add(led);
  return g;
}

/** Networking appliance: a low 1U box with a row of ports. */
function networkPlaceholder([w, h, d]: readonly [number, number, number]): Group {
  const g = new Group();
  g.add(withEdges(part(new BoxGeometry(w, h, d), body, 0, h / 2, 0)));
  for (let i = 0; i < 8; i++) g.add(part(new BoxGeometry(w * 0.07, h * 0.35, 0.03), panel, -w * 0.38 + i * w * 0.09, h / 2, d / 2 + 0.01));
  g.add(part(new BoxGeometry(w * 0.12, h * 0.25, 0.03), neutralLed, w * 0.38, h / 2, d / 2 + 0.015, LED_NAME));
  return g;
}

/** Messaging: a long queue body with segment rings. */
function messagingPlaceholder([w, h, d]: readonly [number, number, number]): Group {
  const g = new Group();
  const r = Math.min(h, d) / 2;
  const tube = withEdges(part(new CylinderGeometry(r, r, w, 24), glass, 0, r, 0));
  tube.rotation.z = Math.PI / 2;
  g.add(tube);
  for (let i = 0; i < 4; i++) {
    const ring = part(new CylinderGeometry(r * 1.06, r * 1.06, 0.06, 24), bodyDark, -w * 0.36 + i * w * 0.24, r, 0);
    ring.rotation.z = Math.PI / 2;
    g.add(ring);
  }
  return g;
}

/** Observability: a sensor dome on a base. */
function observabilityPlaceholder([w, h]: readonly [number, number, number]): Group {
  const g = new Group();
  g.add(withEdges(part(new CylinderGeometry(w * 0.5, w * 0.55, h * 0.3, 24), body, 0, h * 0.15, 0)));
  g.add(part(new SphereGeometry(w * 0.38, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), glass, 0, h * 0.3, 0));
  g.add(part(new SphereGeometry(w * 0.12, 16, 12), coreGlow, 0, h * 0.42, 0));
  return g;
}

/** Cloud and external: a faceted cluster. */
function cloudPlaceholder([w, h]: readonly [number, number, number]): Group {
  const g = new Group();
  g.add(withEdges(part(new IcosahedronGeometry(Math.min(w, h) * 0.45, 0), glass, 0, h * 0.5, 0)));
  g.add(part(new IcosahedronGeometry(Math.min(w, h) * 0.2, 0), coreGlow, 0, h * 0.5, 0));
  return g;
}

/** A thin plate for scope types that are loaded as models (VPC, subnet, …). Real scopes are drawn by the layout. */
function platePlaceholder([w, h, d]: readonly [number, number, number]): Group {
  const g = new Group();
  g.add(withEdges(part(new BoxGeometry(w, h, d), bodyDark, 0, h / 2, 0)));
  return g;
}

const cache = new Map<string, Object3D>();

/** The placeholder template for a component. Built once per component type; clone it per instance. */
export function placeholderTemplate(def: ComponentDefinition): Object3D {
  const hit = cache.get(def.component_type);
  if (hit) return hit;
  const f = def.footprint;
  let g: Group;
  if (def.rendering === "layout-plate") g = platePlaceholder(f);
  else if (def.category === "compute") g = serverPlaceholder(f);
  else if (def.category === "application") g = servicePlaceholder(f);
  else if (def.category === "data") g = dataPlaceholder(f);
  else if (def.category === "networking") g = networkPlaceholder(f);
  else if (def.category === "messaging") g = messagingPlaceholder(f);
  else if (def.category === "observability") g = observabilityPlaceholder(f);
  else g = cloudPlaceholder(f);
  g.name = `placeholder:${def.component_type}`;
  g.userData.placeholder = true;
  cache.set(def.component_type, g);
  return g;
}
