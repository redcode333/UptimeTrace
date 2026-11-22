import { useQueries } from "@tanstack/react-query";
import { useApi } from "../../api/context";
import type { SeriesResponse } from "../../api/types";
import type { Agg } from "../../lib/metrics";
import { RANGES, type RangeId } from "../../lib/timeRange";

/** The window a query covers: the selected range ending now, or the same-length range immediately before it. */
export function windowFor(range: RangeId, previous: boolean, nowMs: number = Date.now()): { start: string; end: string } {
  const ms = RANGES.find((r) => r.id === range)!.ms;
  const end = previous ? nowMs - ms : nowMs;
  return { start: new Date(end - ms).toISOString(), end: new Date(end).toISOString() };
}

/**
 * Series for each host over the current window and the previous window, two queries per host. Results are ordered
 * [host0 current, host0 previous, host1 current, host1 previous, ...]. Nothing is filled in: a failed or empty query stays that.
 */
export function useMetricWindows(hostIds: string[], metric: string, agg: Agg, range: RangeId) {
  const api = useApi();
  return useQueries({
    queries: hostIds.flatMap((id) =>
      [false, true].map((previous) => ({
        queryKey: ["metric-window", id, metric, agg, range, previous ? "previous" : "current"],
        queryFn: ({ signal }: { signal: AbortSignal }): Promise<SeriesResponse> =>
          api.series(id, { metric, agg, ...windowFor(range, previous) }, signal),
        refetchInterval: 60_000,
        retry: false,
      })),
    ),
  });
}
