/**
 * Model loading cache. One load per asset URL for the whole session, however many instances use it. A missing or broken
 * asset is remembered as missing (so a fleet of 500 hosts does not make 500 failing requests) and reported, never hidden.
 *
 * The loader is injected, so the cache is tested without WebGL or a network.
 */
import type { Vec3 } from "./types";

export type AssetResult<T> =
  | { status: "ready"; url: string; template: T; anchors: ReadonlyMap<string, Vec3> }
  | { status: "missing"; url: string; reason: string };

export interface AssetCache<T> {
  /** Load once; later calls for the same URL return the same promise and result. */
  load(url: string): Promise<AssetResult<T>>;
  /** The settled result, "loading", or undefined if never requested. */
  peek(url: string): AssetResult<T> | "loading" | undefined;
  /** Forget a missing result so the next load retries (e.g. after the file was added). */
  retry(url: string): void;
  /** Number of loader calls made: one per URL unless retried. */
  loaderCalls(): number;
  subscribe(fn: () => void): () => void;
}

export function createAssetCache<T>(
  loader: (url: string) => Promise<T>,
  extractAnchors: (template: T) => ReadonlyMap<string, Vec3>,
): AssetCache<T> {
  const inflight = new Map<string, Promise<AssetResult<T>>>();
  const settled = new Map<string, AssetResult<T>>();
  const listeners = new Set<() => void>();
  let calls = 0;
  const notify = () => listeners.forEach((l) => l());

  return {
    load(url) {
      const existing = inflight.get(url);
      if (existing) return existing;
      calls += 1;
      const p = loader(url)
        .then((template): AssetResult<T> => ({ status: "ready", url, template, anchors: extractAnchors(template) }))
        .catch((e: unknown): AssetResult<T> => ({ status: "missing", url, reason: describe(e) }))
        .then((r) => {
          settled.set(url, r);
          notify();
          return r;
        });
      inflight.set(url, p);
      return p;
    },
    peek: (url) => settled.get(url) ?? (inflight.has(url) ? "loading" : undefined),
    retry(url) {
      if (settled.get(url)?.status === "missing") {
        settled.delete(url);
        inflight.delete(url);
        notify();
      }
    },
    loaderCalls: () => calls,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

function describe(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === "string") return e;
  return "The model could not be loaded.";
}

/** Anchor nodes in a supplied model are empty objects named `anchor_<port name>`. */
export const ANCHOR_PREFIX = "anchor_";
export function anchorNameOf(nodeName: string): string | null {
  return nodeName.startsWith(ANCHOR_PREFIX) && nodeName.length > ANCHOR_PREFIX.length ? nodeName.slice(ANCHOR_PREFIX.length) : null;
}
