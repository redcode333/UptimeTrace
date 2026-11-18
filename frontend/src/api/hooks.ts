import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useApi } from "./context";
import type { HostListParams } from "./types";

/** Who am I, and am I allowed to read telemetry? Needs only a valid session. */
export function useMe() {
  const api = useApi();
  return useQuery({ queryKey: ["me"], queryFn: ({ signal }) => api.me(signal), retry: false, staleTime: 60_000 });
}

export function useHosts(params: HostListParams) {
  const api = useApi();
  return useQuery({
    queryKey: ["hosts", params],
    queryFn: ({ signal }) => api.hosts(params, signal),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
}

/** Counts by source and health for the whole fleet (facets ignore their own filter), without loading any rows. */
export function useFleetFacets() {
  return useHosts({ page_size: 1 });
}

import { rangeWindow, type RangeId } from "../lib/timeRange";
import type { Agg } from "../lib/metrics";

/** Host inventory. A new query key per filter set; react-query aborts the superseded request, so a slow old response can never replace a newer one. */
export function useHostList(params: HostListParams, opts: { refetchInterval?: number | false } = {}) {
  const api = useApi();
  return useQuery({
    queryKey: ["hosts", params],
    queryFn: ({ signal }) => api.hosts(params, signal),
    placeholderData: keepPreviousData,
    refetchInterval: opts.refetchInterval ?? 15_000,
  });
}

export function useHost(hostId: string) {
  const api = useApi();
  return useQuery({ queryKey: ["host", hostId], queryFn: ({ signal }) => api.host(hostId, signal), refetchInterval: 15_000, retry: false });
}

/** Current values: the latest endpoint, refreshed often. */
export function useLatest(hostId: string) {
  const api = useApi();
  return useQuery({ queryKey: ["latest", hostId], queryFn: ({ signal }) => api.latest(hostId, signal), refetchInterval: 15_000, retry: false });
}

/** Historical series. The window is computed when the request runs (so refreshes slide forward) but the key holds only the range id. */
export function useSeries(hostId: string | undefined, metric: string, range: RangeId, agg: Agg) {
  const api = useApi();
  return useQuery({
    queryKey: ["series", hostId, metric, range, agg],
    enabled: !!hostId,
    queryFn: ({ signal }) => api.series(hostId!, { metric, agg, ...rangeWindow(range) }, signal),
    // No keepPreviousData: a different host or metric must never show the previous selection's chart while loading.
    refetchInterval: 60_000,
    retry: false,
  });
}
