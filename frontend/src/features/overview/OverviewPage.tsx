import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { UseQueryResult } from "@tanstack/react-query";
import { useAlerts, useHostList, useAlertHistogram, useDistribution, useRankings } from "../../api/hooks";
import type { Alert, Distribution as UtilDistribution, Host, HealthStatus, HostList, Source, Thresholds } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { StaleBanner } from "../../components/ui/Banners";
import { HEALTH_ORDER, HealthBadge, SourceBadge, healthLabel } from "../../components/ui/Badges";
import { UtilCell } from "../../components/ui/UtilCell";
import { ago } from "../../lib/format";
import {
  ATTENTION_ORDER, alertsInScope, hostCoverage, indexHosts, isAllSelected, reasonText, thresholdRule,
} from "../../lib/commandCenter";
import { SOURCES, SOURCE_LABEL, scopeFilter, useScope } from "../../lib/scope";
import { RANGES, globalSearch, useTimeRange } from "../../lib/timeRange";
import { ActiveAlertsPanel } from "./ActiveAlertsPanel";
import { ConnectedPanel } from "./ConnectedPanel";
import { RecentTransitionsPanel } from "./RecentTransitionsPanel";
import { AlertHistogramChart, HostHexMap, InsightList, RankedList, type DataState } from "./CommandCharts";
import { bucketMsFor, insightsFrom } from "./commandCenterModel";

const FLEET_LIMIT = 200; // API maximum page size
const ATTENTION_ROWS = 10;
const ALERT_LIMIT = 200;
const BUCKETS = [
  { lo: 0, hi: 20, label: "0–20" }, { lo: 20, hi: 40, label: "20–40" }, { lo: 40, hi: 60, label: "40–60" },
  { lo: 60, hi: 80, label: "60–80" }, { lo: 80, hi: 101, label: "80–100" },
];
const ATTENTION_HEALTH = ATTENTION_ORDER.filter((h) => h !== "healthy") as HealthStatus[];

export interface Distribution { buckets: number[]; noData: number; max: { value: number; host: Host } | null }

/** Histogram of one latest-utilisation field over the hosts. Hosts with no fresh measurement are counted separately, never as 0. */
export function distribution(hosts: Host[], pick: (h: Host) => number | null): Distribution {
  const buckets = BUCKETS.map(() => 0);
  let noData = 0;
  let max: Distribution["max"] = null;
  for (const h of hosts) {
    const v = pick(h);
    if (v == null) { noData++; continue; }
    const i = BUCKETS.findIndex((b) => v >= b.lo && v < b.hi);
    buckets[i < 0 ? 0 : i]!++;
    if (!max || v > max.value) max = { value: v, host: h };
  }
  return { buckets, noData, max };
}

function Kpi({ label, value, to, tone, hint }: { label: string; value: number | string; to: string; tone?: string; hint?: string }) {
  return (
    <Link to={to} className={`kpi${tone ? ` kpi-${tone}` : ""}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value mono">{value}</span>
      {hint && <span className="kpi-hint dim">{hint}</span>}
    </Link>
  );
}

export function OverviewPage() {
  const [params] = useSearchParams();
  const { selected, toggle, canDeselect } = useScope();
  const scope = scopeFilter(selected);
  const carry = globalSearch(params);
  const now = Date.now();
  const joinNeeded = !isAllSelected(selected);
  const [map3d, setMap3d] = useState(true);
  const { range } = useTimeRange();
  const rangeDef = RANGES.find((r) => r.id === range) ?? RANGES[1]!;

  const summary = useHostList({ source: scope, page_size: 1 });                       // CC-1, CC-3 (exact facets)
  const fleet = useHostList({ source: scope, page_size: FLEET_LIMIT, sort: "hostname" }); // CC-7
  // CC-4: the server orders the whole in-scope fleet by severity and returns the top rows with the exact total.
  const attention = useHostList({ source: scope, health: ATTENTION_HEALTH, sort: "attention", page_size: ATTENTION_ROWS }); // CC-4
  const live = useHostList({ source: ["live"], page_size: FLEET_LIMIT, sort: "hostname" }, { enabled: selected.includes("live") }); // CC-2 (connected panel)
  // LIVE population counts: the server counts every LIVE host, not a loaded page. Requested only when LIVE is in scope.
  const liveSummary = useHostList({ source: ["live"], page_size: 1 }, { enabled: selected.includes("live") });
  // Server-side aggregations over the selected window and the in-scope fleet (complete; not a loaded page).
  const histogram = useAlertHistogram(rangeDef.ms, bucketMsFor(rangeDef.ms) / 1000, scope);
  const cpuRank = useRankings("system.cpu.utilization", scope);
  const memRank = useRankings("system.memory.utilization", scope);
  // Source membership for the alert join. Requested without a source filter ONLY when a subset is selected, because an alert
  // in scope can only be recognised by the source of its host, whatever the scope. Its rows are used for source lookup only.
  const allHosts = useHostList({ page_size: FLEET_LIMIT, sort: "hostname" }, { enabled: joinNeeded });
  const activeAlerts = useAlerts("active", ALERT_LIMIT);                             // CC-5
  const allAlerts = useAlerts("all", ALERT_LIMIT);                                   // CC-6

  const hostIndex = joinNeeded && allHosts.data ? indexHosts(allHosts.data.items) : null;
  const hostsComplete = allHosts.data ? (hostCoverage(allHosts.data)?.complete ?? false) : false;
  // Alerts in the selected sources. An alert whose host is not in the loaded host list cannot be placed, so it is not counted.
  const scopedActive = alertsInScope(activeAlerts.data?.alerts ?? [], selected, hostIndex);
  const activeCoverage = alertsNote(activeAlerts.data, scopedActive.notLoaded, joinNeeded, hostsComplete, "active");

  const to = (health?: HealthStatus | string) => {
    const p = new URLSearchParams(carry.slice(1));
    if (health) p.set("health", health);
    const s = p.toString();
    return `/hosts${s ? `?${s}` : ""}`;
  };
  const headerActions = summary.dataUpdatedAt
    ? <span className="dim">Updated {ago((now - summary.dataUpdatedAt) / 1000)} · refreshes every 15s</span>
    : undefined;

  const s = summary.data;
  const fleetTotal = s ? SOURCES.reduce((n, k) => n + s.facets.source[k], 0) : 0;
  // Global empty states replace the page. Otherwise every panel renders on its own, so a failed request blanks only its panel.
  const fleetEmpty = s !== undefined && fleetTotal === 0;
  const scopeEmpty = s !== undefined && fleetTotal > 0 && s.total === 0;
  const showPanels = !fleetEmpty && !scopeEmpty;

  return (
    <>
      <PageHeader title="Command Center" subtitle="Infrastructure overview" actions={headerActions} />

      {/* CC-1: overall state */}
      {summary.isPending && <Loading label="Loading fleet" />}
      {summary.isError && !s && <ErrorState error={summary.error} onRetry={() => summary.refetch()} />}
      {fleetEmpty && (
        <EmptyState
          title="No hosts have reported yet"
          body="Hosts appear here after an agent sends its first telemetry envelope. Register a host on Agents, then start the agent with its credential."
        >
          <Link to="/onboarding">Set up your first host</Link>
        </EmptyState>
      )}
      {scopeEmpty && (
        <EmptyState
          title="No hosts in the selected sources"
          body="Widen the data-source scope in the top bar to include other hosts."
        />
      )}
      {s && showPanels && (
        <>
          <StaleBanner failed={summary.isError} updatedAt={summary.dataUpdatedAt} onRetry={() => summary.refetch()} />
          <div className="kpis" aria-label="Host health">
            <Kpi label="Total hosts" value={s.total} to={to()} />
            <Kpi label="Healthy" value={s.facets.health.healthy} to={to("healthy")} tone="ok" />
            <Kpi label="Warning" value={s.facets.health.warning} to={to("warning")} tone="warn" />
            <Kpi label="Critical" value={s.facets.health.critical} to={to("critical")} tone="crit" />
            <Kpi label="Offline" value={s.facets.health.offline} to={to("offline")} tone="off" />
            {s.facets.health.stale > 0 && <Kpi label="Stale" value={s.facets.health.stale} to={to("stale")} hint="telemetry later than expected" />}
            {s.facets.health.unknown > 0 && <Kpi label="Unknown" value={s.facets.health.unknown} to={to("unknown")} hint="no fresh data to judge" />}
          </div>
          <HealthBar counts={s.facets.health} total={s.total} />
          <LiveTiles live={liveSummary} selected={selected} to={to} />
          <div className="cc-row cc-row-a">
            <section className="widget cc-card" aria-label="Host map">
              <div className="widget-head">
                <h2>Host map · colour = confirmed state{map3d ? " · height = CPU now" : ""}</h2>
                <div className="seg sm" role="group" aria-label="Map view">
                  <button type="button" aria-pressed={!map3d} onClick={() => setMap3d(false)}>2D</button>
                  <button type="button" aria-pressed={map3d} onClick={() => setMap3d(true)}>3D</button>
                </div>
              </div>
              <div className="widget-body"><HostHexMap hosts={fleet.data?.items ?? []} total={fleet.data?.total} carry={carry} threeD={map3d} state={stateOf(fleet)} /></div>
            </section>
            <section className="widget cc-card" aria-label="CPU now by host">
              <div className="widget-head"><h2>CPU now · by host</h2><span className="dim mono">latest sample</span></div>
              <div className="widget-body"><RankedList label="CPU now by host" data={cpuRank.data} state={stateOf(cpuRank)} carry={carry} /></div>
            </section>
            <section className="widget cc-card" aria-label="Alerts per 15 minutes">
              <div className="widget-head"><h2>Alerts and incidents</h2><span className="dim mono">per 15 min</span></div>
              <div className="widget-body"><AlertHistogramChart data={histogram.data} state={stateOf(histogram)} rangeLabel={rangeDef.label} /></div>
            </section>
          </div>
          <div className="cc-row cc-row-b">
            <section className="widget cc-card" aria-label="Top memory consumers">
              <div className="widget-head"><h2>Top memory · now</h2><span className="dim mono">latest sample</span></div>
              <div className="widget-body"><RankedList label="Top memory consumers" data={memRank.data} state={stateOf(memRank)} carry={carry} /></div>
            </section>
            {/* CC-4: hosts needing attention */}
            <AttentionPanel query={attention} carry={carry} to={to} />
            <section className="widget cc-card" aria-label="Insights">
              <div className="widget-head"><h2>Insights</h2><span className="dim mono">evidence-backed</span></div>
              <div className="widget-body">
                <InsightList insights={insightsFrom(scopedActive.alerts, fleet.data?.items ?? [], (id) => fleet.data?.items.find((h) => h.host_id === id)?.hostname)} />
                {activeCoverage && <p className="dim small cc-foot">{activeCoverage}</p>}
                {summary.data && <p className="dim small cc-foot">{summary.data.facets.health.offline} offline in scope (server count). These cards list loaded hosts only.</p>}
              </div>
            </section>
          </div>
        </>
      )}
      {summary.isError && !s && <p className="dim small">Overall counts are unavailable; the panels below load on their own.</p>}

      {/* CC-2: connected infrastructure */}
      {showPanels && (
        <ConnectedPanel query={selected.includes("live") ? live : undefined} selected={selected} carry={carry} />
      )}

      {showPanels && (
        <>
          <div className="grid2">
            {/* CC-5: active alerts */}
            <ActiveAlertsPanel query={activeAlerts} hostIndex={hostIndex} hostsComplete={hostsComplete} selected={selected} carry={carry} now={now} />
            {/* CC-6: recent alert transitions */}
            <RecentTransitionsPanel query={allAlerts} hostIndex={hostIndex} hostsComplete={hostsComplete} selected={selected} carry={carry} now={now} />
          </div>
          {/* CC-7: utilisation distribution (server-side, whole in-scope fleet) */}
          <UtilisationPanel scope={scope} thresholds={summary.data?.thresholds} carry={carry} />
        </>
      )}

      {/* CC-3: data sources and provenance, last */}
      {s && fleetTotal > 0 && (
        <section className="widget" aria-label="Data sources">
          <div className="widget-head"><h2>Data sources</h2><span className="dim">whole fleet, ignores scope</span></div>
          <div className="widget-body">
            {SOURCES.map((src: Source) => {
              const inScope = selected.includes(src);
              return (
                <div key={src} className="srcrow">
                  <SourceBadge source={src} />
                  <span className="mono">{s.facets.source[src]}</span>
                  <span className="dim">{inScope ? "in scope" : "excluded by scope"}</span>
                  <button type="button" className="btn" aria-label={`${inScope ? "Remove" : "Add"} ${SOURCE_LABEL[src]} ${inScope ? "from" : "to"} scope`}
                    disabled={inScope && !canDeselect} onClick={() => toggle(src)}>
                    {inScope ? "Remove from scope" : "Add to scope"}
                  </button>
                </div>
              );
            })}
            <p className="dim small">
              Counts and panels cover only the sources in scope. LIVE hosts are connected infrastructure; SYNTHETIC and REPLAY hosts are never presented as live.
              Source labels are derived from host IDs; stored provenance is not implemented yet, so these labels are not verified.
            </p>
          </div>
        </section>
      )}
    </>
  );
}

/**
 * The design's second tile group: LIVE hosts only, counted by the server over the whole LIVE population. When LIVE is not in the
 * selected sources the tiles say so instead of showing a number that would not follow the scope.
 */
function LiveTiles({ live, selected, to }: { live: UseQueryResult<HostList>; selected: readonly Source[]; to: (health?: string) => string }) {
  if (!selected.includes("live")) {
    return (
      <div className="cc-live" aria-label="LIVE only">
        <span className="cc-live-title">LIVE only · hidden by the data-source scope</span>
        <p className="dim small">Add LIVE to the data-source scope to count connected infrastructure.</p>
      </div>
    );
  }
  if (live.isPending) return <div className="cc-live" aria-label="LIVE only"><span className="cc-live-title">LIVE only</span><p className="dim small">Loading LIVE hosts…</p></div>;
  if (live.isError && !live.data) return <div className="cc-live" aria-label="LIVE only"><span className="cc-live-title">LIVE only</span><p className="dim small">LIVE counts unavailable: the request failed.</p></div>;
  // Connected = LIVE and reporting within the freshness threshold (server-side). Falls back to the LIVE total for older responses.
  const total = live.data?.facets.connected ?? live.data?.total ?? 0;
  const health = live.data?.facets.health;
  return (
    <div className="cc-live" aria-label="LIVE only">
      <span className="cc-live-title">LIVE only · connected hosts in this workspace</span>
      <div className="cc-live-tiles">
        <Link className="kpi" to={to()}>
          <span className="kpi-label">Connected hosts</span>
          <span className="kpi-value mono">{total}</span>
          <span className="kpi-hint dim">every LIVE host, counted by the server</span>
        </Link>
        <Link className="kpi" to={to("warning")}>
          <span className="kpi-label">LIVE warning</span>
          <span className="kpi-value mono">{health?.warning ?? 0}</span>
          <span className="kpi-hint dim">{health ? `${health.healthy} healthy · ${health.offline} offline` : ""}</span>
        </Link>
      </div>
    </div>
  );
}

/** Says when alert counts cover only part of the data: the newest alerts loaded, or alerts whose host is not loaded. */
function alertsNote(
  data: { alerts: Alert[]; total: number } | undefined,
  notLoaded: number,
  joinNeeded: boolean,
  hostsComplete: boolean,
  what: string,
): string | null {
  if (!data) return null;
  const parts: string[] = [];
  if (data.alerts.length < data.total) parts.push(`Covers the ${data.alerts.length} newest of ${data.total} alerts; older ones ${what} are not counted.`);
  if (joinNeeded && (notLoaded > 0 || !hostsComplete)) parts.push(`${notLoaded} alerts belong to hosts outside the loaded host list and are not counted.`);
  return parts.length ? parts.join(" ") : null;
}

/** Pending or failed request state for a panel. Data that is present is shown even while a refresh is running. */
function stateOf(q: { isPending: boolean; isError: boolean; data: unknown }): DataState {
  if (q.data !== undefined) return null;
  if (q.isError) return "failed";
  if (q.isPending) return "pending";
  return null;
}

function HealthBar({ counts, total }: { counts: Record<HealthStatus, number>; total: number }) {
  return (
    <section className="widget health-bar" aria-label="Host health distribution">
      <div className="widget-head"><h2>Host health distribution</h2><span className="dim mono">{total} in scope</span></div>
      <div className="widget-body">
        <div className="stack" role="img" aria-label={HEALTH_ORDER.map((k) => `${counts[k]} ${k}`).join(", ")}>
          {HEALTH_ORDER.filter((k) => counts[k] > 0).map((k) => <i key={k} className={`seg-${k}`} style={{ flexGrow: counts[k] }} title={`${healthLabel(k)}: ${counts[k]}`} />)}
        </div>
        <ul className="legend">{HEALTH_ORDER.map((k) => <li key={k}><HealthBadge status={k} /> <span className="mono">{counts[k]}</span></li>)}</ul>
      </div>
    </section>
  );
}

/** CC-4: hosts needing attention. Heading count is exact; ordering covers only the loaded rows. */
function AttentionPanel({ query, carry, to }: { query: UseQueryResult<HostList>; carry: string; to: (h?: string) => string }) {
  const data = query.data;
  const rows = data ? data.items : [];
  const total = data?.total ?? 0;
  return (
    <section className="widget" aria-label="Hosts needing attention">
      <div className="widget-head">
        <h2>Hosts needing attention</h2>
        <span className="dim mono">{data ? (total > ATTENTION_ROWS ? `Top ${ATTENTION_ROWS} of ${total} not healthy` : `${total} not healthy`) : ""}</span>
      </div>
      <div className="widget-body">
        {query.isPending && <Loading />}
        {query.isError && !data && <ErrorState error={query.error} onRetry={() => query.refetch()} />}
        <StaleBanner failed={query.isError && !!data} updatedAt={query.dataUpdatedAt} onRetry={() => query.refetch()} />
        {data && total === 0 && <p className="dim">Every host in scope is healthy.</p>}
      </div>
      {rows.length > 0 && (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Host</th><th>Source</th><th>Health</th><th>Why</th><th className="num">CPU</th><th className="num">Memory</th><th className="num">Disk</th><th>Last seen</th></tr></thead>
            <tbody>
              {rows.map((x) => (
                <tr key={x.host_id}>
                  <td><Link className="hostlink" to={`/hosts/${encodeURIComponent(x.host_id)}${carry}`}>{x.hostname}</Link></td>
                  <td><SourceBadge source={x.source} /></td>
                  <td><HealthBadge status={x.health.status} /></td>
                  <td className="small">{reasonText(x)}</td>
                  <td className="num"><LimitCell value={x.latest.cpu_percent} rule={thresholdRule(data?.thresholds, "system.cpu.utilization")} /></td>
                  <td className="num"><LimitCell value={x.latest.memory_percent} rule={thresholdRule(data?.thresholds, "system.memory.utilization")} /></td>
                  <td className="num"><LimitCell value={x.latest.disk_percent_max} rule={thresholdRule(data?.thresholds, "system.disk.utilization")} /></td>
                  <td className="small dim">{ago(x.last_seen_age_seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {total > 0 && <p className="small more"><Link to={to(ATTENTION_HEALTH.join(","))}>View all {total} not healthy</Link></p>}
        </div>
      )}
    </section>
  );
}

/** A utilisation cell coloured only by the configured limit for its metric. No configured limit means no colour. */
function LimitCell({ value, rule }: { value: number | null; rule: { warning: number; critical: number } | undefined }) {
  return <UtilCell value={value} neutral={!rule} warn={rule?.warning ?? 0} crit={rule?.critical ?? 0} />;
}

/** CC-7: utilisation distribution, coloured only by the configured thresholds (the same rule the health states use). */
/** Utilisation distribution from the server: every in-scope host, not the loaded page. Each metric has its own loading and failure state. */
function UtilisationPanel({ scope, thresholds, carry }: { scope: Source[] | undefined; thresholds: Thresholds | undefined; carry: string }) {
  const cpu = useDistribution("cpu", scope);
  const mem = useDistribution("memory", scope);
  const disk = useDistribution("disk_max", scope);
  const total = cpu.data?.total_in_scope ?? mem.data?.total_in_scope ?? disk.data?.total_in_scope;
  return (
    <section className="widget" aria-label="Utilisation distribution">
      <div className="widget-head"><h2>Utilisation distribution</h2><span className="dim mono">{total !== undefined ? `${total} hosts in scope` : ""}</span></div>
      <div className="widget-body">
        <div className="util-grid">
          <UtilPanel title="CPU" metric="system.cpu.utilization" q={cpu} thresholds={thresholds} carry={carry} />
          <UtilPanel title="Memory" metric="system.memory.utilization" q={mem} thresholds={thresholds} carry={carry} />
          <UtilPanel title="Disk (worst mount)" metric="system.disk.utilization" q={disk} thresholds={thresholds} carry={carry} />
        </div>
      </div>
    </section>
  );
}

function UtilPanel({ title, metric, q, thresholds, carry }: {
  title: string; metric: string; q: UseQueryResult<UtilDistribution>; thresholds: Thresholds | undefined; carry: string;
}) {
  if (q.isPending) return <div className="util-panel"><h3 className="small">{title}</h3><Loading /></div>;
  if (q.isError && !q.data) {
    return <div className="util-panel"><h3 className="small">{title}</h3><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>;
  }
  const d = q.data!;
  const rule = thresholdRule(thresholds, metric);
  const top = Math.max(1, ...d.buckets);
  return (
    <div className="util-panel">
      <h3 className="small">{title}</h3>
      <StaleBanner failed={q.isError} updatedAt={q.dataUpdatedAt} onRetry={() => q.refetch()} />
      {d.total_in_scope === 0 ? (
        <p className="dim">No hosts in scope.</p>
      ) : (
        <>
          <div className="hist" role="list">
            {d.bucket_labels.map((label, i) => (
              <div key={label} className="hist-row" role="listitem" aria-label={`${label} percent: ${d.buckets[i]} hosts`}>
                <span className="mono dim">{label}%</span>
                <span className="hist-bar"><i style={{ width: `${((d.buckets[i] ?? 0) / top) * 100}%` }} /></span>
                <span className="mono">{d.buckets[i]}</span>
              </div>
            ))}
          </div>
          <div className="hist-foot dim small">
            {rule ? (
              <span>
                Warning {d.states.warning} · Critical {d.states.critical} · Normal {d.states.normal}
                {" "}(limits: warning ≥ {rule.warning}%, critical ≥ {rule.critical}%)
              </span>
            ) : (
              <span>No configured limit for this metric; states are not judged.</span>
            )}
            {d.no_data > 0 && <span> · {d.no_data} with no current value</span>}
            {d.max && (
              <span> · Highest: <Link to={`/hosts/${encodeURIComponent(d.max.host_id)}${carry}`}>{d.max.hostname}</Link>{" "}
                <span className="mono">{d.max.value.toFixed(1)}%</span></span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
