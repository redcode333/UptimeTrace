/** HTTP client for the backend. No React and no Clerk import: the token source is injected so this stays testable. */
import type { LatestMetrics, HostDetail, HostList, HostListParams, Me, SeriesResponse } from "./types";

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

  async function send(path: string, token: string | null, signal?: AbortSignal): Promise<Response> {
    try {
      return await doFetch(`${baseUrl}${path}`, {
        method: "GET",
        headers: { Accept: "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
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

  async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
    let res = await send(path, await getToken(), signal);
    if (res.status === 401) {
      // A short-lived session token may have expired between getToken() and the server: refresh once, then give up.
      res = await send(path, await getToken({ skipCache: true }), signal);
      if (res.status === 401) {
        onUnauthorized();
        throw await toError(res);
      }
    }
    if (!res.ok) throw await toError(res);
    return (await res.json()) as T;
  }

  const enc = encodeURIComponent;
  return {
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
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;
