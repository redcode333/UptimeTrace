/** Response shapes of the existing backend read API (backend/app/monitoring/schemas.py). Nothing here is invented. */
export type Source = "live" | "synthetic" | "replay";
export type HealthStatus = "healthy" | "warning" | "critical" | "offline" | "unknown";

export interface Reason {
  metric: string;
  labels: Record<string, string>;
  value: number;
  threshold: number;
  level: "warning" | "critical";
}
export interface Health {
  status: HealthStatus;
  reasons: Reason[];
}
export interface MetricSample {
  name: string;
  kind: "gauge" | "counter";
  unit: string;
  labels: Record<string, string>;
  value: number;
  observed_at: string;
  stale: boolean;
  rate_per_second: number | null;
}
export interface LatestSummary {
  cpu_percent: number | null;
  memory_percent: number | null;
  disk_percent_max: number | null;
  disk_mount_max: string | null;
  network_rx_bytes_per_second: number | null;
  network_tx_bytes_per_second: number | null;
}
export interface Host {
  host_id: string;
  hostname: string;
  agent_id: string;
  service: string | null;
  source: Source;
  connected_infrastructure: boolean;
  first_seen: string;
  last_seen: string;
  last_seen_age_seconds: number;
  reporting_status: "reporting" | "silent" | "replay";
  health: Health;
  latest: LatestSummary;
}
export interface HostDetail extends Host {
  metrics: MetricSample[];
}
export interface Thresholds {
  offline_after_seconds: number;
  stale_after_seconds: number;
  rules: Record<string, { warning: number; critical: number }>;
}
export interface HostList {
  items: Host[];
  page: number;
  page_size: number;
  total: number;
  facets: { source: Record<Source, number>; health: Record<HealthStatus, number> };
  thresholds: Thresholds;
}
export interface SeriesPoint {
  t: string;
  value: number | null;
  samples: number;
}
export interface Series {
  labels: Record<string, string>;
  unit: string;
  value_type: "gauge" | "counter_rate";
  points: SeriesPoint[];
}
export interface SeriesResponse {
  host_id: string;
  source: Source;
  metric: string;
  kind: "gauge" | "counter" | null;
  start: string;
  end: string;
  step_seconds: number;
  agg: "avg" | "min" | "max" | "last";
  series: Series[];
}
export interface Me {
  user_id: string;
  authorized: boolean;
}

export interface HostListParams {
  search?: string;
  source?: Source[];
  health?: HealthStatus[];
  sort?: "last_seen" | "hostname";
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

export interface LatestMetrics {
  host_id: string;
  source: Source;
  last_seen: string;
  metrics: MetricSample[];
}
