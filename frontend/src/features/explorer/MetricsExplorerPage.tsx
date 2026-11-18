import { useSearchParams } from "react-router-dom";
import { useHostList, useSeries } from "../../api/hooks";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { StaleBanner } from "../../components/ui/Banners";
import { SourceBadge } from "../../components/ui/Badges";
import { TimeSeriesChart } from "../../components/ui/TimeSeriesChart";
import { timestamp } from "../../lib/format";
import { AGGS, METRICS, metricDef, type Agg } from "../../lib/metrics";
import { scopeFilter, useScope } from "../../lib/scope";
import { RANGES, useTimeRange } from "../../lib/timeRange";

/** Metrics Explorer: host, metric and aggregation live in the URL (shareable); source scope and time range are the global controls. */
export function MetricsExplorerPage() {
  const [params, setParams] = useSearchParams();
  const { selected } = useScope();
  const { range } = useTimeRange();
  const hostsQ = useHostList({ source: scopeFilter(selected), page_size: 200, sort: "hostname" });
  const hosts = hostsQ.data?.items ?? [];

  const metric = metricDef(params.get("metric") ?? "") ?? METRICS[0]!;
  const agg: Agg = (AGGS as readonly string[]).includes(params.get("agg") ?? "") ? (params.get("agg") as Agg) : "avg";
  const wanted = params.get("host");
  // A host that fell out of the source scope is not silently kept: the selection moves to the first host in scope.
  const host = hosts.find((h) => h.host_id === wanted) ?? hosts[0];
  const set = (k: string, v: string, def?: string) => setParams((p) => { const n = new URLSearchParams(p); if (v === def) n.delete(k); else n.set(k, v); return n; }, { replace: true });

  const q = useSeries(host?.host_id, metric.name, range, agg);
  const d = q.data;
  const hasPoints = !!d && d.series.some((s) => s.points.some((p) => p.value != null));
  const rangeLabel = RANGES.find((r) => r.id === range)?.label;

  return (
    <>
      <PageHeader title="Metrics Explorer" subtitle="Inspect one metric on one host" />
      {hostsQ.isPending && <Loading label="Loading hosts" />}
      {hostsQ.isError && !hostsQ.data && <ErrorState error={hostsQ.error} onRetry={() => hostsQ.refetch()} />}
      {hostsQ.data && hosts.length === 0 && <EmptyState title="No hosts in scope" body="There is nothing to explore for the selected data sources. Widen the source scope in the top bar." />}
      {host && (
        <>
          <div className="toolbar">
            <label className="field">
              <span className="dim">Host</span>
              <select value={host.host_id} onChange={(e) => set("host", e.target.value)} aria-label="Host">
                {hosts.map((h) => <option key={h.host_id} value={h.host_id}>{h.hostname} [{h.source.toUpperCase()}]</option>)}
              </select>
            </label>
            <label className="field">
              <span className="dim">Metric</span>
              <select value={metric.name} onChange={(e) => set("metric", e.target.value, METRICS[0]!.name)} aria-label="Metric">
                {METRICS.map((m) => <option key={m.name} value={m.name}>{m.label}</option>)}
              </select>
            </label>
            <label className="field">
              <span className="dim">Aggregation</span>
              <select value={agg} onChange={(e) => set("agg", e.target.value, "avg")} aria-label="Aggregation">
                {AGGS.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </label>
            <span className="dim">Range: last {rangeLabel} (top bar)</span>
          </div>

          <section className="widget" aria-label="Chart">
            <div className="widget-head">
              <h2>{metric.label} · {host.hostname}</h2>
              <span className="idrow"><SourceBadge source={host.source} /><span className="dim mono">{d ? `${d.agg} per ${d.step_seconds}s` : ""}</span></span>
            </div>
            <div className="widget-body">
              {q.isPending && <Loading label="Loading series" />}
              {q.isError && !d && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
              {d && <StaleBanner failed={q.isError} updatedAt={q.dataUpdatedAt} onRetry={() => q.refetch()} />}
              {d && !hasPoints && (
                <EmptyState
                  title="No data in this window"
                  body={`${host.hostname} reported no ${metric.label.toLowerCase()} samples in the last ${rangeLabel} (last seen ${timestamp(host.last_seen)}).${host.source === "replay" ? " Replayed history may lie outside the selected range." : ""} Try a longer range.`}
                />
              )}
              {d && hasPoints && (
                <TimeSeriesChart series={d.series} unit={metric.unit} start={d.start} end={d.end} label={`${metric.label} for ${host.hostname}, ${agg}, last ${rangeLabel}`} />
              )}
              {metric.note && <p className="dim small">{metric.note} Gaps in the line mean no valid rate (missing samples or a counter reset), not zero traffic.</p>}
            </div>
          </section>
        </>
      )}
    </>
  );
}
