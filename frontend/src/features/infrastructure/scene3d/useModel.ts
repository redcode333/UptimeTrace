import { useEffect, useMemo, useSyncExternalStore } from "react";
import type { Object3D } from "three";
import { modelCache } from "../../../lib/scene3d/modelAssets";
import { registry } from "../../../lib/scene3d/registry";
import type { ComponentDefinition, Vec3 } from "../../../lib/scene3d/types";
import { placeholderTemplate } from "./placeholders";

export type ModelSource = "asset" | "placeholder" | "loading";

export interface ModelState {
  source: ModelSource;
  url: string;
  /** Template to clone per instance. Null while the supplied model is still loading. */
  template: Object3D | null;
  /** Anchors read from the supplied model; null for placeholders (registry ports are used). */
  anchors: ReadonlyMap<string, Vec3> | null;
  reason: string | null;
}

const baseUrl = () => (typeof import.meta !== "undefined" && import.meta.env?.BASE_URL) || "/";

function stateFor(def: ComponentDefinition, url: string, r: ReturnType<typeof modelCache.peek>): ModelState {
  if (r && r !== "loading" && r.status === "ready") return { source: "asset", url, template: r.template, anchors: r.anchors, reason: null };
  if (r && r !== "loading" && r.status === "missing") return { source: "placeholder", url, template: placeholderTemplate(def), anchors: null, reason: r.reason };
  return { source: "loading", url, template: null, anchors: null, reason: null };
}

/** The model for a component type: the supplied GLB once loaded, otherwise a clearly identified placeholder. */
export function useModel(componentType: string): ModelState {
  const def = registry.require(componentType);
  const url = registry.assetUrl(componentType, baseUrl());
  const loadable = def.rendering === "model";
  useEffect(() => { if (loadable) void modelCache.load(url); }, [url, loadable]);
  const r = useSyncExternalStore(modelCache.subscribe, () => modelCache.peek(url), () => undefined);
  return useMemo(() => (loadable ? stateFor(def, url, r) : { source: "placeholder" as const, url, template: null, anchors: null, reason: "Drawn by the layout" }), [def, url, r, loadable]);
}

export interface ModelStatus {
  componentType: string;
  displayName: string;
  url: string;
  source: ModelSource;
  instances: number;
  reason: string | null;
}

/** Status of every model in use, for the "development placeholder" notice. */
export function useModelStatuses(usage: ReadonlyMap<string, number>): ModelStatus[] {
  // Scope plates are drawn by the layout; no model file is requested or reported for them.
  const types = [...usage.keys()].filter((t) => registry.require(t).rendering === "model").sort();
  const key = types.join("|");
  useEffect(() => {
    for (const t of types) void modelCache.load(registry.assetUrl(t, baseUrl()));
    // types is captured by key
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const snapshot = useSyncExternalStore(
    modelCache.subscribe,
    () => types.map((t) => {
      const r = modelCache.peek(registry.assetUrl(t, baseUrl()));
      return r === undefined ? "u" : r === "loading" ? "l" : r.status;
    }).join("|"),
    () => "",
  );
  return useMemo(() => types.map((t) => {
    const def = registry.require(t);
    const url = registry.assetUrl(t, baseUrl());
    const s = stateFor(def, url, modelCache.peek(url));
    return { componentType: t, displayName: def.display_name, url, source: s.source, instances: usage.get(t) ?? 0, reason: s.reason };
    // snapshot changes when any status changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [key, snapshot, usage]);
}
