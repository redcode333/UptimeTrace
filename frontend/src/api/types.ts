/** Response shapes of the existing backend read API (backend/app/monitoring/schemas.py). Nothing here is invented. */
export type Source = "live" | "synthetic" | "replay";
export type HealthStatus = "healthy" | "warning" | "critical" | "stale" | "offline" | "unknown";
/** Coarse grouping: warning and critical are both DEGRADED. */
export type HealthCategory = "healthy" | "degraded" | "stale" | "offline" | "unknown";

export interface Reason {
  metric: string;
  labels: Record<string, string>;
  value: number;
  threshold: number;
  level: "warning" | "critical";
  /** Stable condition identity (metric + labels). */
  key: string;
}
export interface Health {
  status: HealthStatus;
  category: HealthCategory;
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
  reporting_status: "reporting" | "stale" | "silent" | "replay";
  health: Health;
  latest: LatestSummary;
}
export interface HostDetail extends Host {
  metrics: MetricSample[];
}
export interface Thresholds {
  host_stale_after_seconds: number;
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

/** Host registry (backend/app/registry/router.py). Tokens appear only in IssuedCredential, once. */
export type RegistryHostKind = "live" | "synthetic" | "replay";
export type TelemetryState = "awaiting_first_telemetry" | "reporting" | "stale" | "silent" | "replay";
export interface RegistryHost {
  host_id: string;
  kind: RegistryHostKind;
  display_name: string | null;
  created_at: string;
  created_by: string;
  deactivated_at: string | null;
  telemetry_state: TelemetryState;
  last_envelope_at: string | null;
  active_credentials: number;
}
export interface Credential {
  id: string;
  name: string;
  agent_id: string;
  created_at: string;
  created_by: string;
  revoked_at: string | null;
  revoked_by: string | null;
}
export interface IssuedCredential extends Credential {
  /** The plaintext bearer token. Returned once; the server cannot show it again. */
  token: string;
}
export interface RegisterHostBody {
  host_id: string;
  kind: RegistryHostKind;
  display_name?: string;
}
export interface IssueCredentialBody {
  name?: string;
  agent_id?: string;
}

// Alerts (persistent; raised by the backend evaluator, never by the browser).
export type AlertState = "open" | "acknowledged" | "resolved";
export type AlertSeverity = "warning" | "critical";
export type AlertRule = "threshold" | "host_offline";
export type AlertStatusFilter = "active" | "resolved" | "all";

export interface Alert {
  id: string;
  host_id: string;
  condition_key: string;
  rule: AlertRule;
  metric: string | null;
  labels: Record<string, string>;
  severity: AlertSeverity;
  state: AlertState;
  value: number | null;
  threshold: number | null;
  opened_at: string;
  last_evaluated_at: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  resolved_at: string | null;
  resolved_by: string | null;
  resolution: "recovered" | "manual" | null;
}

export interface AlertEvent {
  event: "opened" | "severity_changed" | "acknowledged" | "recovered" | "resolved_manually";
  occurred_at: string;
  actor: string;
  detail: Record<string, unknown>;
}

export interface AlertDetail extends Alert {
  events: AlertEvent[];
}

/** GET /api/v1/settings/monitoring: the configured rules and freshness windows in force (defaults if nothing is stored). */
export interface MonitoringSettings {
  config: {
    freshness: { stale_after_seconds: number; offline_after_seconds: number };
    rules: Record<string, { warning: number; critical: number }>;
  };
  defaults: MonitoringSettings["config"];
  version: number;
  updated_at: string | null;
  updated_by: string | null;
}

/** PUT /api/v1/settings/monitoring body. `expected_version` must match the saved version (0 when nothing is saved). */
export interface MonitoringSettingsIn {
  config: MonitoringSettings["config"];
  expected_version: number;
}

/** GET /api/v1/registry/mcp-tokens: metadata only. The raw token is never returned after creation. */
export interface McpToken {
  id: string;
  name: string;
  scopes: string[];
  created_at: string;
  created_by: string;
  expires_at: string;
  revoked_at: string | null;
  revoked_by: string | null;
}

/** POST response: the record plus the raw token, which is shown once. */
export interface IssuedMcpToken extends McpToken {
  token: string;
}

export interface IssueMcpTokenIn {
  name: string;
  scopes: string[];
  expires_in_days: number;
}

export interface AlertList {
  alerts: Alert[];
  total: number;
}

// Investigation (GET /api/v1/alerts/{id}/investigation). Null or empty means the evidence was not recorded, never a guess.
export interface InvestigationPoint {
  t: string;
  value: number | null;
  samples: number;
}
export interface InvestigationSeries {
  window: "around_trigger" | "around_recovery";
  start: string;
  end: string;
  step_seconds: number;
  metric: string | null;
  labels: Record<string, string>;
  points: InvestigationPoint[];
}
export interface InvestigationRelated {
  id: string;
  rule: AlertRule;
  metric: string | null;
  labels: Record<string, string>;
  severity: AlertSeverity;
  state: AlertState;
  opened_at: string;
  resolution: "recovered" | "manual" | null;
}
export interface Investigation {
  as_of: string;
  window_minutes: number;
  alert: {
    id: string; host_id: string; state: AlertState; severity: AlertSeverity; rule: AlertRule; metric: string | null;
    labels: Record<string, string>; condition_key: string; acknowledged_by: string | null; acknowledged_at: string | null;
    resolved_by: string | null; resolution: "recovered" | "manual" | null;
  };
  trigger: {
    rule: AlertRule; metric: string | null; labels: Record<string, string>; severity: AlertSeverity; comparison: string | null;
    threshold: number | null; trigger_value: number | null; trigger_observed_at: string | null; first_fired_at: string;
    last_evaluated_at: string; unavailable: string[];
  };
  recovery: {
    recovered: boolean; resolution: "recovered" | "manual" | null; resolved_by: string | null; resolved_at: string | null;
    value: number | null; observed_at: string | null;
  };
  host: {
    host_id: string; known: boolean;
    current: { health: { status: HealthStatus }; reporting_status: string; last_seen: string; source: string } | null;
  };
  series: InvestigationSeries[];
  series_note: string | null;
  events: { event: AlertEvent["event"]; occurred_at: string; actor: string; detail: Record<string, unknown> }[];
  related: { window_minutes: number; total: number; limit: number; offset: number; note: string; items: InvestigationRelated[] };
  settings: { version: number; warning_limit: number | null; critical_limit: number | null; stale_after_seconds: number; offline_after_seconds: number };
}
export interface InvestigationRecord {
  id: string;
  alert_id: string;
  created_at: string;
  created_by: string;
  schema_version: string;
  evidence_digest: string;
  digest_matches: boolean;
  rederivation_matches: boolean;
  verified: boolean;
  body: {
    generated_at: string;
    generated_by: string;
    facts: { id: string; text: string; source: string }[];
    conclusions: { id: string; text: string; basis: string[] }[];
    not_established: string[];
  };
}
