/**
 * Workspace-wide data-source scope (LIVE / SYNTHETIC / REPLAY), kept in the URL as ?source=live,synthetic.
 * Rules (approved design): at least one source stays selected; no param means all three.
 * Pages pass `selected` to the API as the `source` filter, so every list, count and chart follows the same scope.
 */
import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import type { Source } from "../api/types";

export const SOURCES: readonly Source[] = ["live", "synthetic", "replay"];
export const SOURCE_LABEL: Record<Source, string> = { live: "LIVE", synthetic: "SYNTHETIC", replay: "REPLAY" };

export function parseScope(params: URLSearchParams): Source[] {
  const raw = params.get("source");
  if (!raw) return [...SOURCES];
  const wanted = new Set(raw.split(",").map((s) => s.trim().toLowerCase()));
  const picked = SOURCES.filter((s) => wanted.has(s));
  return picked.length > 0 ? picked : [...SOURCES]; // unknown or empty values fall back to "all", never to "none"
}

/** Toggle one source. Turning off the last selected source is refused (returns the same selection). */
export function toggleSource(selected: readonly Source[], source: Source): Source[] {
  if (selected.includes(source)) {
    if (selected.length === 1) return [...selected];
    return selected.filter((s) => s !== source);
  }
  return SOURCES.filter((s) => s === source || selected.includes(s));
}

export function withScope(params: URLSearchParams, selected: readonly Source[]): URLSearchParams {
  const next = new URLSearchParams(params);
  if (selected.length === SOURCES.length) next.delete("source");
  else next.set("source", selected.join(","));
  return next;
}

export function useScope() {
  const [params, setParams] = useSearchParams();
  const selected = parseScope(params);
  const toggle = useCallback(
    (source: Source) => setParams((p) => withScope(p, toggleSource(parseScope(p), source)), { replace: true }),
    [setParams],
  );
  return { selected, toggle, isAll: selected.length === SOURCES.length, canDeselect: selected.length > 1 };
}

/** The `source` API filter for a scope. All three selected means no filter (identical result, shorter URL). */
export function scopeFilter(selected: readonly Source[]): Source[] | undefined {
  return selected.length === SOURCES.length ? undefined : [...selected];
}
