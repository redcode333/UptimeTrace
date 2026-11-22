// @vitest-environment node
/**
 * The supplied GLB models, read back through the same loader and helpers the renderer uses. These tests load the real
 * files from public/assets/infrastructure, so a broken or mis-named model fails here rather than in the browser.
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Group, Mesh, Object3D } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { describe, expect, it } from "vitest";
import { anchorNameOf, createAssetCache } from "./assetCache";
import { ENTITY_BINDING } from "./catalogue";
import { resolveAnchor } from "./connections";
import { extractAnchors, instantiate, measure } from "./modelAssets";
import { joinBase, registry } from "./registry";

// Vitest runs from the frontend folder (npm test); jsdom gives import.meta.url a non-file scheme, so it is not used here.
const PUBLIC = join(process.cwd(), "public");

/** The supplied models, keyed by `<category>/<component_type>`. Each file is bound to exactly one registry type. */
const SUPPLIED = ["compute/physical-server", "application/microservice", "networking/network-switch", "data/postgresql"] as const;

function parseGlb(rel: string): Promise<Group> {
  const buf = readFileSync(join(PUBLIC, "assets", "infrastructure", `${rel}.glb`));
  const data = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  return new Promise((resolve, reject) => {
    new GLTFLoader().parse(data, "", (gltf) => resolve(gltf.scene), (err) => reject(err));
  });
}

const typeOf = (rel: string) => rel.split("/")[1]!;

function meshOf(root: Object3D): Mesh {
  let found: Mesh | undefined;
  root.traverse((n) => { if (!found && (n as Mesh).isMesh) found = n as Mesh; });
  if (!found) throw new Error("no mesh in model");
  return found;
}

describe("supplied model files", () => {
  it.each(SUPPLIED)("%s is at the path its registry type declares", (rel) => {
    const def = registry.require(typeOf(rel));
    expect(def.asset_path).toBe(`/assets/infrastructure/${rel}.glb`);
    expect(existsSync(join(PUBLIC, "assets", "infrastructure", `${rel}.glb`))).toBe(true);
  });

  it("resolves model URLs under the app's base path", () => {
    expect(registry.assetUrl("physical-server", "/")).toBe("/assets/infrastructure/compute/physical-server.glb");
    expect(registry.assetUrl("microservice", "/app/")).toBe("/app/assets/infrastructure/application/microservice.glb");
    expect(joinBase("/app/", "/assets/x.glb")).toBe("/app/assets/x.glb");
  });

  it("is loaded as one model per type, and no other catalogue type has a file yet", () => {
    const modelTypes = registry.all().filter((d) => d.rendering === "model").map((d) => d.component_type);
    const withFile = modelTypes.filter((t) => {
      const rel = registry.require(t).asset_path.replace("/assets/infrastructure/", "");
      return existsSync(join(PUBLIC, "assets", "infrastructure", rel));
    });
    // Every other model type keeps the development placeholder, so the missing-model fallback still applies to them.
    expect(withFile.sort()).toEqual(["microservice", "network-switch", "physical-server", "postgresql"]);
  });
});

describe("component-to-model mapping", () => {
  it("binds hosts to the physical-server model and services to the microservice model", () => {
    expect(ENTITY_BINDING.host).toBe("physical-server");
    expect(ENTITY_BINDING.service).toBe("microservice");
    expect(registry.forEntity("host").component_type).toBe("physical-server");
    expect(registry.forEntity("service").component_type).toBe("microservice");
  });

  it("does not draw network switches or PostgreSQL, because no inventory entity is bound to them", () => {
    // Only real entities are drawn; a registry type is not shown on its own. Binding a new entity kind is a separate change.
    expect(Object.values(ENTITY_BINDING)).not.toContain("network-switch");
    expect(Object.values(ENTITY_BINDING)).not.toContain("postgresql");
    expect(registry.require("network-switch").rendering).toBe("model");
    expect(registry.require("postgresql").rendering).toBe("model");
  });

  it("maps the PostgreSQL model to exactly the postgresql registry type, with no aliases", () => {
    const def = registry.require("postgresql");
    expect(def.asset_path).toBe("/assets/infrastructure/data/postgresql.glb");
    expect(def.footprint).toEqual([1.0, 1.1, 1.0]);
    // No other database type shares this file or gets an alias to it.
    for (const other of ["mysql", "redis", "database-cluster", "database-replica", "mongodb"]) {
      expect(registry.require(other).asset_path).not.toBe(def.asset_path);
    }
  });
});

describe("loaded models", () => {
  it.each(SUPPLIED)("%s parses as a model whose size matches its registry footprint", async (rel) => {
    const scene = await parseGlb(rel);
    const size = measure(scene);
    const footprint = registry.require(typeOf(rel)).footprint;
    // Within a centimetre on every axis: the small vent and faceplate details account for the difference.
    size.forEach((v, i) => expect(Math.abs(v - footprint[i]!)).toBeLessThan(0.01));
    expect(meshOf(scene).geometry.getAttribute("position").count).toBeGreaterThan(0);
  });

  it.each(SUPPLIED)("%s has an anchor at every registry port, at the same offset", async (rel) => {
    const scene = await parseGlb(rel);
    const anchors = extractAnchors(scene);
    const def = registry.require(typeOf(rel));
    for (const port of def.connection_ports) {
      const at = anchors.get(port.name);
      expect(at, `anchor_${port.name}`).toBeDefined();
      at!.forEach((v, i) => expect(Math.abs(v - port.offset[i]!)).toBeLessThan(1e-3));
    }
  });

  it("anchor resolution uses the supplied model's anchor, not the registry fallback", async () => {
    const scene = await parseGlb("compute/physical-server");
    const def = registry.require("physical-server");
    const resolved = resolveAnchor(def, "top", extractAnchors(scene));
    expect(resolved.origin).toBe("model");
    expect(resolved.offset[1]).toBeCloseTo(0.7, 3);
  });
});

describe("model loading, caching and instances", () => {
  it("loads each file once however many instances request it", async () => {
    const cache = createAssetCache(() => parseGlb("compute/physical-server"), extractAnchors);
    const url = registry.assetUrl("physical-server", "/");
    const results = await Promise.all(Array.from({ length: 25 }, () => cache.load(url)));
    expect(cache.loaderCalls()).toBe(1);
    expect(new Set(results).size).toBe(1);
    expect(results[0]!.status).toBe("ready");
  });

  it("instances share the template's geometry and material without changing one another", async () => {
    const template = await parseGlb("compute/physical-server");
    const a = instantiate(template);
    const b = instantiate(template);

    expect(meshOf(a).geometry).toBe(meshOf(template).geometry);
    expect(meshOf(a).material).toBe(meshOf(template).material);
    expect(meshOf(b).geometry).toBe(meshOf(a).geometry);

    a.position.set(5, 0, 2);
    a.userData.entityId = "host-a";
    b.userData.entityId = "host-b";
    expect(b.position.toArray()).toEqual([0, 0, 0]);
    expect(a.userData.entityId).toBe("host-a");
    expect(b.userData.entityId).toBe("host-b");
  });

  it("hides anchor nodes on instances but leaves the template's anchors visible", async () => {
    const template = await parseGlb("application/microservice");
    const instance = instantiate(template);
    const anchorNodes = (root: Object3D) => {
      const out: Object3D[] = [];
      root.traverse((n) => { if (anchorNameOf(n.name)) out.push(n); });
      return out;
    };
    expect(anchorNodes(instance).length).toBeGreaterThan(0);
    expect(anchorNodes(instance).every((n) => !n.visible)).toBe(true);
    expect(anchorNodes(template).every((n) => n.visible)).toBe(true);
  });
});
