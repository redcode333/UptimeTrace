import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { ApiProvider } from "../api/context";
import type { ApiClient } from "../api/client";
import type { HostList } from "../api/types";

export const facets = (over: Partial<HostList["facets"]["source"]> = {}): HostList => ({
  items: [],
  page: 1,
  page_size: 1,
  total: 36,
  facets: {
    source: { live: 3, synthetic: 31, replay: 2, ...over },
    health: { healthy: 29, warning: 3, critical: 2, stale: 0, offline: 2, unknown: 0 },
  },
  thresholds: { host_stale_after_seconds: 90, offline_after_seconds: 300, stale_after_seconds: 300, rules: {} },
});

export function fakeApi(over: Partial<ApiClient> = {}): ApiClient {
  return {
    me: async () => ({ user_id: "user_test_1", authorized: true }),
    hosts: async () => facets(),
    host: async () => { throw new Error("not used"); },
    latest: async () => { throw new Error("not used"); },
    series: async () => { throw new Error("not used"); },
    alertHistogram: async (p) => ({
      start: p.start, end: p.end, bucket_seconds: p.bucket_seconds, sources: p.source ?? ["live", "synthetic", "replay"],
      buckets: [], total: 0, critical: 0, warning: 0, complete: true,
    }),
    distribution: async (p) => ({
      metric: p.metric, sources: p.source ?? ["live", "synthetic", "replay"], total_in_scope: 0, bucket_labels: ["0–20", "20–40", "40–60", "60–80", "80–100"],
      buckets: [0, 0, 0, 0, 0], no_data: 0, states: { normal: 0, warning: 0, critical: 0, none: 0 }, max: null, complete: true,
    }),
    rankings: async (p) => ({
      metric: p.metric, sources: p.source ?? ["live", "synthetic", "replay"], total_in_scope: 0, with_value: 0, without_value: 0,
      limit: p.limit ?? 8, items: [], complete: true,
    }),
    platformHealth: async () => ({ http: 200, status: "ok", app: "ok", database: "ok" }),
    infrastructure: async () => ({ environments: [], unassigned_regions: [], unassigned_clusters: [], ungrouped_hosts: [], services: [], unassigned_services: [] }),
    registryHosts: async () => [],
    registerHost: async () => { throw new Error("not used"); },
    credentials: async () => [],
    issueCredential: async () => { throw new Error("not used"); },
    revokeCredential: async () => { throw new Error("not used"); },
    ...over,
  } as ApiClient;
}

export function renderApp(ui: ReactElement, { api = fakeApi(), route = "/" }: { api?: ApiClient; route?: string } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ApiProvider client={api}>
        <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
      </ApiProvider>
    </QueryClientProvider>,
  );
}
