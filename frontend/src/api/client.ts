/** HTTP client for the backend. No React and no Clerk import: the token source is injected so this stays testable. */
import type {
  Alert, AlertDetail, AlertHistogram, AlertList, Distribution, DistributionMetric, PlatformHealth, AlertStatusFilter, Credential, RankedMetric, Ranking, Source, HostDetail, HostList, HostListParams, IssueCredentialBody, IssuedCredential,
  Investigation, InvestigationRecord, TraceDetail, Inventory, IssueMcpTokenIn, IssuedMcpToken, LatestMetrics, Me, McpToken, MonitoringSettings, MonitoringSettingsIn, RegisterHostBody, RegistryHost, SeriesResponse,
} from "./types";
import type { Rehearsal, RehearsalScenario, RehearsalTarget, StartRehearsalIn } from "./rehearsalTypes";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: { path: string; message: string }[] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
  get isNetwork() {
    return this.status === 0;
  }
}

export type TokenGetter = (options?: { skipCache?: boolean }) => Promise<string | null>;

export interface ApiClientOptions {
  baseUrl: string;
  getToken: TokenGetter;
  /** Called once a request is still 401 after a forced token refresh: the session is over. */
  onUnauthorized: () => void;
  fetchImpl?: typeof fetch;
}

type Param = string | number | string[] | undefined;

function query(params: Record<string, Param>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || (Array.isArray(v) && v.length === 0)) continue;
    q.set(k, Array.isArray(v) ? v.join(",") : String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export function createApiClient({ baseUrl, getToken, onUnauthorized, fetchImpl }: ApiClientOptions) {
  const doFetch = fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));

  async function send(method: string, path: string, token: string | null, body?: unknown, signal?: AbortSignal): Promise<Response> {
    try {
      return await doFetch(`${baseUrl}${path}`, {
        method,
        headers: {
          Accept: "application/json",
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal,
      });
    } catch (e) {
      if ((e as Error).name === "AbortError") throw e;
      throw new ApiError(0, "network_error", "Could not reach the UptimeTrace API.");
    }
  }

  async function toError(res: Response): Promise<ApiError> {
    try {
      const body = await res.json();
      const err = body?.error;
      if (err && typeof err.code === "string") {
        return new ApiError(res.status, err.code, String(err.message ?? ""), err.details ?? []);
      }
    } catch {
      /* not the error envelope */
    }
    return new ApiError(res.status, "http_error", `Request failed (${res.status}).`);
  }

  async function call<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    let res = await send(method, path, await getToken(), body, signal);
    if (res.status === 401) {
      // A short-lived session token may have expired between getToken() and the server: refresh once, then give up.
      // A 401 means the request was not processed, so replaying a mutation is safe.
      res = await send(method, path, await getToken({ skipCache: true }), body, signal);
      if (res.status === 401) {
        onUnauthorized();
        throw await toError(res);
      }
    }
    if (!res.ok) throw await toError(res);
    return (await res.json()) as T;
  }

  const get = <T,>(path: string, signal?: AbortSignal) => call<T>("GET", path, undefined, signal);
  /** The public health probe. It sends no credentials and keeps the body of a 503, which is how a degraded dependency reports itself. */
  async function platformHealth(signal?: AbortSignal): Promise<PlatformHealth> {
    let res: Response;
    try {
      res = await doFetch(`${baseUrl}/health`, { headers: { Accept: "application/json" }, signal });
    } catch (e) {
      if ((e as Error).name === "AbortError") throw e;
      throw new ApiError(0, "network_error", "Could not reach the UptimeTrace API.");
    }
    let body: { status?: unknown; app?: unknown; database?: unknown } | null = null;
    try { body = await res.json(); } catch { body = null; }
    if (!body || typeof body.status !== "string") {
      throw new ApiError(res.status, "http_error", `The health check answered ${res.status} without a status.`);
    }
    return {
      http: res.status,
      status: body.status,
      app: typeof body.app === "string" ? body.app : "unknown",
      database: typeof body.database === "string" ? body.database : "unknown",
    };
  }
  const enc = encodeURIComponent;
  return {
    platformHealth,
    me: (signal?: AbortSignal) => get<Me>("/api/v1/me", signal),
    hosts: (p: HostListParams = {}, signal?: AbortSignal) => get<HostList>(`/api/v1/hosts${query({ ...p })}`, signal),
    host: (id: string, signal?: AbortSignal) => get<HostDetail>(`/api/v1/hosts/${enc(id)}`, signal),
    latest: (id: string, signal?: AbortSignal) =>
      get<LatestMetrics>(`/api/v1/hosts/${enc(id)}/metrics/latest`, signal),
    series: (
      id: string,
      p: { metric: string; start?: string; end?: string; step?: number; agg?: string },
      signal?: AbortSignal,
    ) => get<SeriesResponse>(`/api/v1/hosts/${enc(id)}/metrics/series${query(p)}`, signal),
    // Host registry. The plaintext token is in the response to issueCredential only.
    registryHosts: (signal?: AbortSignal) => get<RegistryHost[]>("/api/v1/registry/hosts", signal),
    // Read-only: the configured thresholds and freshness windows. Editing is not part of this client yet.
    monitoringSettings: (signal?: AbortSignal) => get<MonitoringSettings>("/api/v1/settings/monitoring", signal),
    saveMonitoringSettings: (body: MonitoringSettingsIn) => call<MonitoringSettings>("PUT", "/api/v1/settings/monitoring", body),
    // Infrastructure inventory: grouping only. Hosts, telemetry and alerts are never changed by these calls.
    infrastructure: (signal?: AbortSignal) => get<Inventory>("/api/v1/infrastructure", signal),
    createEnvironment: (name: string) => call<{ id: string }>("POST", "/api/v1/infrastructure/environments", { name }),
    updateEnvironment: (id: string, name: string) => call<{ id: string }>("PATCH", `/api/v1/infrastructure/environments/${enc(id)}`, { name }),
    deleteEnvironment: (id: string) => call<unknown>("DELETE", `/api/v1/infrastructure/environments/${enc(id)}`),
    createRegion: (body: { name: string; environment_id: string | null }) => call<{ id: string }>("POST", "/api/v1/infrastructure/regions", body),
    updateRegion: (id: string, body: { name?: string; environment_id?: string | null }) => call<{ id: string }>("PATCH", `/api/v1/infrastructure/regions/${enc(id)}`, body),
    deleteRegion: (id: string) => call<unknown>("DELETE", `/api/v1/infrastructure/regions/${enc(id)}`),
    createCluster: (body: { name: string; region_id: string | null }) => call<{ id: string }>("POST", "/api/v1/infrastructure/clusters", body),
    updateCluster: (id: string, body: { name?: string; region_id?: string | null }) => call<{ id: string }>("PATCH", `/api/v1/infrastructure/clusters/${enc(id)}`, body),
    deleteCluster: (id: string) => call<unknown>("DELETE", `/api/v1/infrastructure/clusters/${enc(id)}`),
    assignClusterHost: (clusterId: string, hostId: string) => call<unknown>("POST", `/api/v1/infrastructure/clusters/${enc(clusterId)}/hosts`, { host_id: hostId }),
    removeClusterHost: (clusterId: string, hostId: string) => call<unknown>("DELETE", `/api/v1/infrastructure/clusters/${enc(clusterId)}/hosts/${enc(hostId)}`),
    createService: (body: { name: string; description?: string | null }) => call<{ id: string }>("POST", "/api/v1/infrastructure/services", body),
    updateService: (id: string, body: { name?: string; description?: string | null }) => call<{ id: string }>("PATCH", `/api/v1/infrastructure/services/${enc(id)}`, body),
    deleteService: (id: string) => call<unknown>("DELETE", `/api/v1/infrastructure/services/${enc(id)}`),
    linkServiceHost: (serviceId: string, hostId: string) => call<unknown>("POST", `/api/v1/infrastructure/services/${enc(serviceId)}/hosts`, { host_id: hostId }),
    unlinkServiceHost: (serviceId: string, hostId: string) => call<unknown>("DELETE", `/api/v1/infrastructure/services/${enc(serviceId)}/hosts/${enc(hostId)}`),
    // Failure rehearsals: a fixed scenario against a registered synthetic host. Starting needs the typed host confirmation.
    rehearsalCatalogue: (signal?: AbortSignal) => get<{ scenarios: RehearsalScenario[] }>("/api/v1/rehearsals/catalogue", signal),
    rehearsalTargets: (signal?: AbortSignal) => get<RehearsalTarget[]>("/api/v1/rehearsals/targets", signal),
    rehearsals: (signal?: AbortSignal, alertId?: string) =>
      get<{ rehearsals: Rehearsal[] }>(`/api/v1/rehearsals${query({ alert_id: alertId })}`, signal),
    rehearsal: (id: string, signal?: AbortSignal) => get<Rehearsal>(`/api/v1/rehearsals/${enc(id)}`, signal),
    startRehearsal: (body: StartRehearsalIn) => call<Rehearsal>("POST", "/api/v1/rehearsals", body),
    stopRehearsal: (id: string) => call<Rehearsal>("POST", `/api/v1/rehearsals/${enc(id)}/stop`),
    // MCP access tokens (read-only MCP server). Metadata list, one-time issue, revoke.
    mcpTokens: (signal?: AbortSignal) => get<McpToken[]>("/api/v1/registry/mcp-tokens", signal),
    issueMcpToken: (body: IssueMcpTokenIn) => call<IssuedMcpToken>("POST", "/api/v1/registry/mcp-tokens", body),
    revokeMcpToken: (id: string) => call<McpToken>("DELETE", `/api/v1/registry/mcp-tokens/${enc(id)}`),
    registerHost: (body: RegisterHostBody) => call<RegistryHost>("POST", "/api/v1/registry/hosts", body),
    credentials: (hostId: string, signal?: AbortSignal) => get<Credential[]>(`/api/v1/registry/hosts/${enc(hostId)}/credentials`, signal),
    issueCredential: (hostId: string, body: IssueCredentialBody) =>
      call<IssuedCredential>("POST", `/api/v1/registry/hosts/${enc(hostId)}/credentials`, body),
    revokeCredential: (hostId: string, credentialId: string) =>
      call<Credential>("DELETE", `/api/v1/registry/hosts/${enc(hostId)}/credentials/${enc(credentialId)}`),
    // Alerts. Raised and resolved by the backend; the browser only reads them and records acknowledgement or manual resolution.
    alerts: (status: AlertStatusFilter = "active", signal?: AbortSignal, limit?: number, hostId?: string) =>
      get<AlertList>(`/api/v1/alerts${query({ status, limit, host_id: hostId })}`, signal),
    alertHistogram: (p: { start: string; end: string; bucket_seconds: number; source?: Source[] }, signal?: AbortSignal) =>
      get<AlertHistogram>(`/api/v1/alerts/histogram${query({ ...p })}`, signal),
    distribution: (p: { metric: DistributionMetric; source?: Source[] }, signal?: AbortSignal) =>
      get<Distribution>(`/api/v1/hosts/distribution${query({ ...p })}`, signal),
    rankings: (p: { metric: RankedMetric; source?: Source[]; limit?: number }, signal?: AbortSignal) =>
      get<Ranking>(`/api/v1/hosts/rankings${query({ ...p })}`, signal),
    alert: (id: string, signal?: AbortSignal) => get<AlertDetail>(`/api/v1/alerts/${enc(id)}`, signal),
    acknowledgeAlert: (id: string) => call<Alert>("POST", `/api/v1/alerts/${enc(id)}/acknowledge`),
    resolveAlert: (id: string) => call<Alert>("POST", `/api/v1/alerts/${enc(id)}/resolve`),
    // Investigation: the evidence view (bounded window) and the persisted record for one alert.
    investigation: (id: string, params: { window_minutes?: number }, signal?: AbortSignal) =>
      get<Investigation>(`/api/v1/alerts/${enc(id)}/investigation${query(params)}`, signal),
    investigationReport: (id: string, signal?: AbortSignal) => get<InvestigationRecord>(`/api/v1/alerts/${enc(id)}/investigation/report`, signal),
    createInvestigationReport: (id: string) => call<InvestigationRecord>("POST", `/api/v1/alerts/${enc(id)}/investigation/report`),
    // One application trace, checked server-side against the caller's workspace.
    trace: (traceId: string, signal?: AbortSignal) => get<TraceDetail>(`/api/v1/traces/${enc(traceId)}`, signal),
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;
