import { Link, useSearchParams } from "react-router-dom";
import { useHostList } from "../../api/hooks";
import type { Host, HealthStatus, Source } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading, NotAvailable } from "../../components/states/States";
import { StaleBanner } from "../../components/ui/Banners";
import { HEALTH_ORDER, HealthBadge, SourceBadge, healthLabel } from "../../components/ui/Badges";
import { UtilCell } from "../../components/ui/UtilCell";
import { DASH, ago, pct } from "../../lib/format";
import { SOURCES, SOURCE_LABEL, scopeFilter, useScope } from "../../lib/scope";
import { globalSearch } from "../../lib/timeRange";

const FLEET_LIMIT = 200; // API maximum page size
const BUCKETS = [
  { lo: 0, hi: 20, label: "0–20" }, { lo: 20, hi: 40, label: "20–40" }, { lo: 40, hi: 60, label: "40–60" },
  { lo: 60, hi: 80, label: "60–80" }, { lo: 80, hi: 101, label: "80–100" },
];

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

function UtilPanel({ title, hosts, pick, carry }: { title: string; hosts: Host[]; pick: (h: Host) => number | null; carry: string }) {
  const d = distribution(hosts, pick);
  const top = Math.max(1, ...d.buckets);
  const withData = hosts.length - d.noData;
  return (
    <section className="widget" aria-label={`${title} overview`}>
      <div className="widget-head"><h2>{title}</h2><span className="dim mono">{withData} of {hosts.length} hosts reporting</span></div>
      <div className="widget-body">
        {withData === 0 ? (
          <p className="dim">No fresh measurements from any host in scope.</p>
        ) : (
          <div className="hist" role="list">
            {BUCKETS.map((b, i) => (
              <div key={b.label} className="hist-row" role="listitem" aria-label={`${b.label} percent: ${d.buckets[i]} hosts`}>
                <span className="mono dim">{b.label}%</span>
                <span className="hist-bar"><i className={b.lo >= 90 ? "crit" : b.lo >= 80 ? "warn" : ""} style={{ width: `${(d.buckets[i]! / top) * 100}%` }} /></span>
                <span className="mono">{d.buckets[i]}</span>
              </div>
            ))}
          </div>
        )}
        <div className="hist-foot dim">
          {d.noData > 0 && <span>{d.noData} host{d.noData === 1 ? "" : "s"} with no data</span>}
          {d.max && <span>Highest: <Link to={`/hosts/${encodeURIComponent(d.max.host.host_id)}${carry}`}>{d.max.host.hostname}</Link> <span className="mono">{pct(d.max.value)}</span></span>}
        </div>
      </div>
    </section>
  );
}

export function OverviewPage() {
  const [params] = useSearchParams();
  const { selected } = useScope();
  const scope = scopeFilter(selected);
  const carry = globalSearch(params);
  const fleet = useHostList({ source: scope, page_size: FLEET_LIMIT, sort: "hostname" });
  const attention = useHostList({ source: scope, health: ["critical", "warning", "offline", "unknown"], page_size: 100 });
  const to = (health?: HealthStatus) => {
    const p = new URLSearchParams(carry.slice(1));
    if (health) p.set("health", health);
    const s = p.toString();
    return `/hosts${s ? `?${s}` : ""}`;
  };

  if (fleet.isPending) return (<><PageHeader title="Command Center" subtitle="Infrastructure overview" /><Loading label="Loading fleet" /></>);
  if (!fleet.data) return (<><PageHeader title="Command Center" subtitle="Infrastructure overview" /><ErrorState error={fleet.error} onRetry={() => fleet.refetch()} /></>);

  const d = fleet.data;
  const hosts = d.items;
  const h = d.facets.health;
  const needs = [...(attention.data?.items ?? [])].sort(
    (a, b) => HEALTH_ORDER.indexOf(a.health.status) - HEALTH_ORDER.indexOf(b.health.status) || a.hostname.localeCompare(b.hostname),
  );
  const silentLive = hosts.filter((x) => x.source === "live" && x.reporting_status === "silent").length;

  return (
    <>
      <PageHeader
        title="Command Center"
        subtitle="Infrastructure overview"
        actions={<span className="dim">Updated {ago((Date.now() - fleet.dataUpdatedAt) / 1000)} · refreshes every 15s</span>}
      />
      <StaleBanner failed={fleet.isError} updatedAt={fleet.dataUpdatedAt} onRetry={() => fleet.refetch()} />

      {d.total === 0 ? (
        <EmptyState
          title={selected.length < SOURCES.length ? "No hosts in the selected sources" : "No hosts have reported yet"}
          body={selected.length < SOURCES.length ? "Widen the data-source scope in the top bar to include other hosts." : "Hosts appear here after an agent sends its first telemetry envelope."}
        />
      ) : (
        <>
          {d.total > hosts.length && (
            <div className="banner warn" role="status">Utilization panels cover the first {hosts.length} of {d.total} hosts. Host counts are exact.</div>
          )}
          <div className="kpis" aria-label="Host health">
            <Kpi label="Total hosts" value={d.total} to={to()} />
            <Kpi label="Healthy" value={h.healthy} to={to("healthy")} tone="ok" />
            <Kpi label="Warning" value={h.warning} to={to("warning")} tone="warn" />
            <Kpi label="Critical" value={h.critical} to={to("critical")} tone="crit" />
            <Kpi label="Offline" value={h.offline} to={to("offline")} tone="off" hint={silentLive ? `${silentLive} live silent` : undefined} />
            {h.unknown > 0 && <Kpi label="Unknown" value={h.unknown} to={to("unknown")} hint="no fresh data to judge" />}
          </div>

          <div className="grid2">
            <section className="widget" aria-label="Data sources">
              <div className="widget-head"><h2>Data sources</h2><span className="dim">whole fleet, ignores scope</span></div>
              <div className="widget-body">
                {SOURCES.map((s: Source) => (
                  <div key={s} className="srcrow">
                    <SourceBadge source={s} />
                    <span className="mono">{d.facets.source[s]}</span>
                    <span className="dim">{selected.includes(s) ? "in scope" : "excluded by scope"}</span>
                  </div>
                ))}
                <p className="dim small">Counts and charts on this page cover only the sources in scope. {SOURCE_LABEL.live} hosts are connected infrastructure; SYNTHETIC and REPLAY hosts are never presented as live.</p>
              </div>
            </section>
            <section className="widget" aria-label="Host health distribution">
              <div className="widget-head"><h2>Host health distribution</h2><span className="dim mono">{d.total} in scope</span></div>
              <div className="widget-body">
                <div className="stack" role="img" aria-label={HEALTH_ORDER.map((k) => `${h[k]} ${k}`).join(", ")}>
                  {HEALTH_ORDER.filter((k) => h[k] > 0).map((k) => <i key={k} className={`seg-${k}`} style={{ flexGrow: h[k] }} title={`${healthLabel(k)}: ${h[k]}`} />)}
                </div>
                <ul className="legend">{HEALTH_ORDER.map((k) => <li key={k}><HealthBadge status={k} /> <span className="mono">{h[k]}</span></li>)}</ul>
              </div>
            </section>
          </div>

          <div className="grid3">
            <UtilPanel title="CPU utilization" hosts={hosts} pick={(x) => x.latest.cpu_percent} carry={carry} />
            <UtilPanel title="Memory utilization" hosts={hosts} pick={(x) => x.latest.memory_percent} carry={carry} />
            <UtilPanel title="Disk utilization (worst mount)" hosts={hosts} pick={(x) => x.latest.disk_percent_max} carry={carry} />
          </div>

          <section className="widget" aria-label="Hosts needing attention">
            <div className="widget-head">
              <h2>Hosts needing attention</h2>
              <span className="dim">{attention.data ? `${attention.data.total} not healthy` : ""}</span>
            </div>
            {attention.isPending && <div className="widget-body"><Loading /></div>}
            {attention.isError && !attention.data && <div className="widget-body"><ErrorState error={attention.error} onRetry={() => attention.refetch()} /></div>}
            {attention.data && needs.length === 0 && <div className="widget-body"><p className="dim">Every host in scope is healthy.</p></div>}
            {needs.length > 0 && (
              <div className="table-wrap">
                <table className="tbl">
                  <thead><tr><th>Host</th><th>Source</th><th>Health</th><th>Why</th><th className="num">CPU</th><th className="num">Memory</th><th className="num">Disk</th><th>Last seen</th></tr></thead>
                  <tbody>
                    {needs.slice(0, 10).map((x) => (
                      <tr key={x.host_id}>
                        <td><Link className="hostlink" to={`/hosts/${encodeURIComponent(x.host_id)}${carry}`}>{x.hostname}</Link></td>
                        <td><SourceBadge source={x.source} /></td>
                        <td><HealthBadge status={x.health.status} /></td>
                        <td className="small">{reasonText(x)}</td>
                        <td className="num"><UtilCell value={x.latest.cpu_percent} /></td>
                        <td className="num"><UtilCell value={x.latest.memory_percent} /></td>
                        <td className="num"><UtilCell value={x.latest.disk_percent_max} warn={90} /></td>
                        <td className="small dim">{ago(x.last_seen_age_seconds)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {attention.data && attention.data.total > 10 && <p className="small more"><Link to={to()}>View all hosts</Link> · showing 10 of {attention.data.total}</p>}
              </div>
            )}
          </section>

          <NotAvailable title="Recent infrastructure activity" reason="No event or incident feed exists in the backend yet, so nothing is shown here rather than an invented timeline." />
        </>
      )}
    </>
  );
}

function reasonText(h: Host): string {
  if (h.health.status === "offline") return `No telemetry for ${ago(h.last_seen_age_seconds).replace(" ago", "")}`;
  if (h.health.status === "unknown") return "No fresh cpu, memory or disk measurement";
  const r = h.health.reasons[0];
  if (!r) return DASH;
  const name = r.metric.replace("system.", "").replace(".utilization", "");
  const more = h.health.reasons.length > 1 ? ` +${h.health.reasons.length - 1} more` : "";
  return `${name}${r.labels.mount ? ` ${r.labels.mount}` : ""} ${pct(r.value)} ≥ ${r.threshold}%${more}`;
}
