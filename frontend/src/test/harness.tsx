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
