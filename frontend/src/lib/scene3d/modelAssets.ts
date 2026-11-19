/**
 * three.js side of the asset pipeline: GLB loading, anchor extraction and per-instance cloning. A loaded model is a
 * template; every instance is a clone that shares the template's geometry and materials, so N hosts cost one download and
 * one set of GPU buffers.
 */
import { Box3, Object3D, Vector3 } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { clone as cloneSkinned } from "three/examples/jsm/utils/SkeletonUtils.js";
import { anchorNameOf, createAssetCache, type AssetCache } from "./assetCache";
import type { Vec3 } from "./types";

/** Positions of `anchor_*` nodes relative to the model root, in the root's local space. */
export function extractAnchors(root: Object3D): Map<string, Vec3> {
  const out = new Map<string, Vec3>();
  root.updateMatrixWorld(true);
  const inverse = root.matrixWorld.clone().invert();
  const v = new Vector3();
  root.traverse((node) => {
    const name = anchorNameOf(node.name);
    if (!name || out.has(name)) return;
    node.getWorldPosition(v).applyMatrix4(inverse);
    out.set(name, [round(v.x), round(v.y), round(v.z)]);
  });
  return out;
}

const round = (n: number) => Math.round(n * 1e4) / 1e4;

/** A new instance of a template. Geometry and materials are shared; transforms and user data are per instance. */
export function instantiate(template: Object3D): Object3D {
  const copy = cloneSkinned(template);
  copy.traverse((n) => {
    if (anchorNameOf(n.name)) n.visible = false;
  });
  return copy;
}

/** Size of a model, used to warn when a supplied model is far from its declared footprint. */
export function measure(root: Object3D): Vec3 {
  const size = new Box3().setFromObject(root).getSize(new Vector3());
  return [round(size.x), round(size.y), round(size.z)];
}

async function loadGlb(url: string): Promise<Object3D> {
  const gltf = await new GLTFLoader().loadAsync(url);
  return gltf.scene;
}

/** The app-wide model cache. */
export const modelCache: AssetCache<Object3D> = createAssetCache(loadGlb, extractAnchors);
