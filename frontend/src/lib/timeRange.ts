/** Global time range, kept in the URL as ?range=6h. Presets only (custom ranges arrive with the Metrics Explorer). */
import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

export const RANGES = [
  { id: "1h", label: "1h", ms: 3_600_000 },
  { id: "6h", label: "6h", ms: 6 * 3_600_000 },
  { id: "24h", label: "24h", ms: 24 * 3_600_000 },
  { id: "7d", label: "7d", ms: 7 * 24 * 3_600_000 },
] as const;
export type RangeId = (typeof RANGES)[number]["id"];
export const DEFAULT_RANGE: RangeId = "6h";

export function parseRange(params: URLSearchParams): RangeId {
  const raw = params.get("range");
  return (RANGES.find((r) => r.id === raw)?.id ?? DEFAULT_RANGE) as RangeId;
}

export function rangeWindow(id: RangeId, now: Date = new Date()): { start: string; end: string } {
  const ms = RANGES.find((r) => r.id === id)!.ms;
  return { start: new Date(now.getTime() - ms).toISOString(), end: now.toISOString() };
}

export function useTimeRange() {
  const [params, setParams] = useSearchParams();
  const range = parseRange(params);
  const setRange = useCallback(
    (id: RangeId) =>
      setParams(
        (p) => {
          const next = new URLSearchParams(p);
          if (id === DEFAULT_RANGE) next.delete("range");
          else next.set("range", id);
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );
  return { range, setRange };
}

/** The query parameters that follow the user from page to page (scope and range). */
export function globalSearch(params: URLSearchParams): string {
  const keep = new URLSearchParams();
  for (const k of ["source", "range"]) {
    const v = params.get(k);
    if (v) keep.set(k, v);
  }
  const s = keep.toString();
  return s ? `?${s}` : "";
}
