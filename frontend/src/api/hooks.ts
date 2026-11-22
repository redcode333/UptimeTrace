import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useApi } from "./context";
import type { AlertStatusFilter, DistributionMetric, Host, HostListParams, InvestigationRecord, IssueCredentialBody, IssueMcpTokenIn, MonitoringSettingsIn, RankedMetric, RegisterHostBody, RegistryHost, Source } from "./types";

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
export function useHostList(params: HostListParams, opts: { refetchInterval?: number | false; enabled?: boolean } = {}) {
  const api = useApi();
  return useQuery({
    queryKey: ["hosts", params],
    queryFn: ({ signal }) => api.hosts(params, signal),
    placeholderData: keepPreviousData,
    refetchInterval: opts.refetchInterval ?? 15_000,
    enabled: opts.enabled ?? true,
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

/** Registered hosts (including ones that have never reported). */
/** Configured monitoring thresholds. Read-only; cached for a minute. */
export function useMonitoringSettings() {
  const api = useApi();
  return useQuery({
    queryKey: ["settings", "monitoring"],
    queryFn: ({ signal }) => api.monitoringSettings(signal),
    staleTime: 60_000,
  });
}

/**
 * Save the thresholds and freshness windows. The cache is updated and dependent queries refreshed only after the server
 * accepts the write. A stale version is refused by the server (409) and nothing is overwritten.
 */
export function useSaveMonitoringSettings() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MonitoringSettingsIn) => api.saveMonitoringSettings(body),
    onSuccess: (saved) => {
      qc.setQueryData(["settings", "monitoring"], saved);
      return Promise.all(["hosts", "host", "alerts", "settings"].map((key) => qc.invalidateQueries({ queryKey: [key] })));
    },
  });
}

/**
 * Every host's monitoring record for the topology. The API caps a page at 200, so pages are read in sequence up to a bounded
 * number. `complete` is false when the bound is reached or the API reports more hosts than were loaded.
 */
export const TOPOLOGY_PAGE_SIZE = 200;
export const TOPOLOGY_MAX_PAGES = 10;
export function useTopologyHosts() {
  const api = useApi();
  return useQuery({
    queryKey: ["topology", "hosts"],
    queryFn: async ({ signal }) => {
      const items: Host[] = [];
      let total = 0;
      for (let page = 1; page <= TOPOLOGY_MAX_PAGES; page++) {
        const r = await api.hosts({ page, page_size: TOPOLOGY_PAGE_SIZE }, signal);
        items.push(...r.items);
        total = r.total;
        if (r.items.length === 0 || items.length >= r.total) break;
      }
      return { items, total };
    },
    refetchInterval: 15_000,
  });
}

/** Infrastructure inventory. Every grouping change refreshes the inventory and the host lists it affects. */
export function useInfrastructure() {
  const api = useApi();
  return useQuery({ queryKey: ["infrastructure"], queryFn: ({ signal }) => api.infrastructure(signal) });
}

export function useInfrastructureMutation<TArgs, TOut>(fn: (api: ReturnType<typeof useApi>, args: TArgs) => Promise<TOut>) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: TArgs) => fn(api, args),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: ["infrastructure"] }), qc.invalidateQueries({ queryKey: ["hosts"] })]),
  });
}

/** MCP access tokens (metadata). */
export function useMcpTokens() {
  const api = useApi();
  return useQuery({ queryKey: ["mcp", "tokens"], queryFn: ({ signal }) => api.mcpTokens(signal) });
}

/**
 * Issue a token. The raw token is returned to the caller only; it is not written to the query cache, so it is not kept in
 * any shared state. The list is refreshed after the server confirms the issue.
 */
export function useIssueMcpToken() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: IssueMcpTokenIn) => api.issueMcpToken(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["mcp", "tokens"] }),
  });
}

export function useRevokeMcpToken() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.revokeMcpToken(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["mcp", "tokens"] }),
  });
}

/** Registry host list. `refetchInterval` lets a caller poll faster while it waits for something (e.g. first telemetry); false stops polling. */
export function useRegistryHosts(opts: { refetchInterval?: number | false } = {}) {
  const api = useApi();
  return useQuery({
    queryKey: ["registry", "hosts"],
    queryFn: ({ signal }) => api.registryHosts(signal),
    refetchInterval: opts.refetchInterval === undefined ? 15_000 : opts.refetchInterval,
  });
}

export function useCredentials(hostId: string | null) {
  const api = useApi();
  return useQuery({ queryKey: ["registry", "credentials", hostId], enabled: !!hostId, queryFn: ({ signal }) => api.credentials(hostId!, signal) });
}

/** Puts a host the server has just registered into the registry list at once. A page that navigates to that host in the same
 *  tick then renders its next step from the response, not from a refetch that is still in flight. */
export function rememberRegisteredHost(qc: QueryClient, host: RegistryHost) {
  qc.setQueryData<RegistryHost[]>(["registry", "hosts"], (prev) => (prev ? [...prev.filter((h) => h.host_id !== host.host_id), host] : prev));
}

/** Mutations refresh the registry and fleet queries. The issued token lives only in the mutation result held by the caller. */
export function useRegistryMutations() {
  const api = useApi();
  const qc = useQueryClient();
  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: ["registry"] }), qc.invalidateQueries({ queryKey: ["hosts"] })]);
  return {
    // Not awaited: react-query runs a mutation's per-call onSuccess only after the hook-level onSuccess promise settles, and
    // `refresh` waits for the registry refetch. The refetch still runs; the page moves on from the response.
    register: useMutation({ mutationFn: (b: RegisterHostBody) => api.registerHost(b), onSuccess: () => { void refresh(); } }),
    issue: useMutation({ mutationFn: (v: { hostId: string; body: IssueCredentialBody }) => api.issueCredential(v.hostId, v.body), onSuccess: refresh }),
    revoke: useMutation({ mutationFn: (v: { hostId: string; credentialId: string }) => api.revokeCredential(v.hostId, v.credentialId), onSuccess: refresh }),
  };
}

/** Alerts for the console. Refreshed every 15 s so a recovery or a new breach shows without a reload. */
export function useAlerts(status: AlertStatusFilter, limit?: number, hostId?: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["alerts", status, limit ?? null, hostId ?? null],
    queryFn: ({ signal }) => api.alerts(status, signal, limit, hostId),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
}

/** Detail (with the timeline) is loaded only for the row that is expanded. */
/**
 * Alerts opened per bucket over a window ending now (rounded up to the minute, so the query key is stable between refreshes).
 * The database counts every qualifying alert; the source scope is passed through unchanged.
 */
export function useAlertHistogram(windowMs: number, bucketSeconds: number, sources: Source[] | undefined) {
  const api = useApi();
  const end = Math.ceil(Date.now() / 60_000) * 60_000;
  return useQuery({
    queryKey: ["alert-histogram", windowMs, bucketSeconds, end, sources ?? null],
    queryFn: ({ signal }) =>
      api.alertHistogram({ start: new Date(end - windowMs).toISOString(), end: new Date(end).toISOString(), bucket_seconds: bucketSeconds, source: sources }, signal),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
}

/** Platform health from GET /health. Refreshed every 30 s; a failed probe is an error, never a healthy result. */
export function usePlatformHealth() {
  const api = useApi();
  return useQuery({
    queryKey: ["platform-health"],
    queryFn: ({ signal }) => api.platformHealth(signal),
    refetchInterval: 30_000,
    retry: false,
  });
}

/** Utilisation distribution over the whole in-scope fleet, computed by the server. */
export function useDistribution(metric: DistributionMetric, sources: Source[] | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["distribution", metric, sources ?? null],
    queryFn: ({ signal }) => api.distribution({ metric, source: sources }, signal),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
}

/** The top hosts for one metric across the whole in-scope fleet, computed by the server. */
export function useRankings(metric: RankedMetric, sources: Source[] | undefined, limit = 8) {
  const api = useApi();
  return useQuery({
    queryKey: ["rankings", metric, sources ?? null, limit],
    queryFn: ({ signal }) => api.rankings({ metric, source: sources, limit }, signal),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
}

export function useAlert(id: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["alert", id],
    enabled: !!id,
    queryFn: ({ signal }) => api.alert(id!, signal),
    refetchInterval: 15_000,
    retry: false,
  });
}

/** Acknowledge or resolve. Both refresh every alert query, so the list and any open timeline agree with the server. */
export function useAlertMutations() {
  const api = useApi();
  const qc = useQueryClient();
  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: ["alerts"] }), qc.invalidateQueries({ queryKey: ["alert"] })]);
  return {
    acknowledge: useMutation({ mutationFn: (id: string) => api.acknowledgeAlert(id), onSuccess: refresh }),
    resolve: useMutation({ mutationFn: (id: string) => api.resolveAlert(id), onSuccess: refresh }),
  };
}

/** The investigation view for one alert. Refreshed so a recovery that lands while the page is open shows without a reload. */
export function useInvestigation(id: string | undefined, windowMinutes: number) {
  const api = useApi();
  return useQuery({
    queryKey: ["investigation", id, windowMinutes],
    queryFn: ({ signal }) => api.investigation(id!, { window_minutes: windowMinutes }, signal),
    enabled: !!id,
    refetchInterval: 15_000,
  });
}

/** One application trace, loaded only when an engineer opens it. */
export function useTrace(traceId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["trace", traceId],
    queryFn: ({ signal }) => api.trace(traceId!, signal),
    enabled: !!traceId,
  });
}

/** The persisted record. A 404 (none created yet) is an answer, not a failure, so it is not retried. */
export function useInvestigationReport(id: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["investigation-report", id],
    queryFn: ({ signal }) => api.investigationReport(id!, signal),
    enabled: !!id,
    retry: false,
  });
}

/** Creates the record once. The server returns the stored record on later calls, so this is safe to repeat. */
export function useCreateInvestigationReport(id: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.createInvestigationReport(id),
    onSuccess: (record: InvestigationRecord) => qc.setQueryData(["investigation-report", id], record),
  });
}
