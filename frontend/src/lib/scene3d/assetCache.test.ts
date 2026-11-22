import { BoxGeometry, Group, Mesh, MeshStandardMaterial, Object3D } from "three";
import { describe, expect, it, vi } from "vitest";
import { anchorNameOf, createAssetCache } from "./assetCache";
import { extractAnchors, instantiate } from "./modelAssets";

/** A model as a GLB would produce it: a mesh plus empty anchor nodes. */
function fakeModel(): Object3D {
  const root = new Group();
  root.name = "physical-server";
  const body = new Mesh(new BoxGeometry(1.4, 0.7, 1), new MeshStandardMaterial());
  body.name = "chassis";
  body.position.y = 0.35;
  const top = new Object3D();
  top.name = "anchor_top";
  top.position.set(0, 0.7, 0);
  const nic = new Object3D();
  nic.name = "anchor_net_out";
  nic.position.set(0.1, 0.2, 0.25);
  body.add(nic); // nested: its position is relative to the chassis
  root.add(body, top);
  return root;
}

describe("asset cache", () => {
  it("calls the loader once per asset however many instances request it", async () => {
    const loader = vi.fn(async () => fakeModel());
    const cache = createAssetCache(loader, extractAnchors);
    const url = "/assets/infrastructure/compute/physical-server.glb";
    const results = await Promise.all(Array.from({ length: 50 }, () => cache.load(url)));
    expect(loader).toHaveBeenCalledTimes(1);
    expect(cache.loaderCalls()).toBe(1);
    expect(new Set(results).size).toBe(1);
    expect(results[0]!.status).toBe("ready");
  });

  it("reports a missing asset, remembers it, and does not retry it for every instance", async () => {
    const loader = vi.fn(async () => { throw new Error("404 Not Found"); });
    const cache = createAssetCache<Object3D>(loader, extractAnchors);
    const url = "/assets/infrastructure/data/redis.glb";
    const a = await cache.load(url);
    const b = await cache.load(url);
    expect(a).toEqual({ status: "missing", url, reason: "404 Not Found" });
    expect(b).toBe(a);
    expect(cache.peek(url)).toBe(a);
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("retries a missing asset only on request", async () => {
    let present = false;
    const cache = createAssetCache(async () => { if (!present) throw new Error("missing"); return fakeModel(); }, extractAnchors);
    const url = "/assets/infrastructure/compute/physical-server.glb";
    expect((await cache.load(url)).status).toBe("missing");
    present = true;
    cache.retry(url);
    expect(cache.peek(url)).toBeUndefined();
    expect((await cache.load(url)).status).toBe("ready");
    expect(cache.loaderCalls()).toBe(2);
  });

  it("notifies subscribers when a load settles", async () => {
    const cache = createAssetCache(async () => fakeModel(), extractAnchors);
    const fn = vi.fn();
    const off = cache.subscribe(fn);
    await cache.load("a.glb");
    expect(fn).toHaveBeenCalledTimes(1);
    off();
    await cache.load("b.glb");
    expect(fn).toHaveBeenCalledTimes(1);
  });
});

describe("model templates and instances", () => {
  it("reads anchor_* nodes as named ports in the model's root space, including nested ones", () => {
    const anchors = extractAnchors(fakeModel());
    expect([...anchors.keys()].sort()).toEqual(["net_out", "top"]);
    expect(anchors.get("top")).toEqual([0, 0.7, 0]);
    expect(anchors.get("net_out")).toEqual([0.1, 0.55, 0.25]);
    expect(anchorNameOf("anchor_")).toBeNull();
    expect(anchorNameOf("chassis")).toBeNull();
  });

  it("gives each instance its own transform while sharing the template's geometry and material", () => {
    const template = fakeModel();
    const a = instantiate(template);
    const b = instantiate(template);
    a.position.set(1, 0, 0);
    b.position.set(5, 0, 0);
    a.userData.entityId = "host:alpha";
    b.userData.entityId = "host:beta";
    const meshOf = (o: Object3D) => o.getObjectByName("chassis") as Mesh;
    expect(a).not.toBe(b);
    expect(meshOf(a)).not.toBe(meshOf(b));
    expect(meshOf(a).geometry).toBe(meshOf(template).geometry);
    expect(meshOf(b).material).toBe(meshOf(template).material);
    expect(a.position.x).toBe(1);
    expect(b.position.x).toBe(5);
    expect(template.userData.entityId).toBeUndefined();
    expect(a.getObjectByName("anchor_top")!.visible).toBe(false);
  });
});
