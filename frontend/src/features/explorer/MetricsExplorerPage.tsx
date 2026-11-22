import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueries } from "@tanstack/react-query";
import { useApi } from "../../api/context";
import { useHostList } from "../../api/hooks";
import type { Host, Series, SeriesPoint, SeriesResponse, Source } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { SourceBadge } from "../../components/ui/Badges";
import { type Unit, TimeSeriesChart } from "../../components/ui/TimeSeriesChart";
import { DASH, bytesPerSecond, timestamp } from "../../lib/format";
import { AGGS, METRICS, metricDef, type Agg } from "../../lib/metrics";
import { SOURCE_LABEL, scopeFilter, useScope } from "../../lib/scope";
import { RANGES, useTimeRange } from "../../lib/timeRange";
import { useMetricWindows } from "./useMetricWindows";

/** Four hosts at most: the chart has four series colours, so each host keeps its own colour. */
export const MAX_HOSTS = 4;
const PREVIOUS = "previous window";

const csv = (raw: string | null): string[] => [...new Set((raw ?? "").split(",").map((s) => s.trim()).filter(Boolean))];
const labelKey = (labels: Record<string, string>) => JSON.stringify(Object.entries(labels).sort(([a], [b]) => a.localeCompare(b)));
const fmt = (unit: Unit, v: number) => (unit === "percent" ? `${v.toFixed(1)}%` : bytesPerSecond(v));

/** Min, average, max and last over the valid points only. A gap is not a value, so it never counts as zero. */
export function statsOf(points: SeriesPoint[] | undefined): { min: number; avg: number; max: number; last: number; lastAt: string } | null {
  const valid = (points ?? []).filter((p): p is SeriesPoint & { value: number } => p.value != null);
  if (valid.length === 0) return null;
  const values = valid.map((p) => p.value);
  return {
    min: Math.min(...values),
    max: Math.max(...values),
    avg: values.reduce((a, b) => a + b, 0) / values.length,
    last: valid[valid.length - 1]!.value,
    lastAt: valid[valid.length - 1]!.t,
  };
}

type Resolved =
  | { id: string; state: "ready"; host: Host }
  | { id: string; state: "pending" }
  | { id: string; state: "outside"; source: Source }
  | { id: string; state: "error"; error: unknown };

/** Metrics Explorer: one metric across up to four hosts. Host, metric, aggregation and compare live in the URL; the source scope and range are the global controls. */
export function MetricsExplorerPage() {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const { selected } = useScope();
  const { range } = useTimeRange();
  const rangeDef = RANGES.find((r) => r.id === range) ?? RANGES[1]!;
  const [typed, setTyped] = useState("");
  const [search, setSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setSearch(typed.trim()), 300);
    return () => clearTimeout(t);
  }, [typed]);

  const hostsQ = useHostList({ source: scopeFilter(selected), search: search || undefined, page_size: 200, sort: "hostname" });
  const pickable = hostsQ.data?.items ?? [];
  const pickableIds = new Set(pickable.map((h) => h.host_id));

  const metric = metricDef(params.get("metric") ?? "") ?? METRICS[0]!;
  const agg: Agg = (AGGS as readonly string[]).includes(params.get("agg") ?? "") ? (params.get("agg") as Agg) : "avg";
  const compare = params.get("compare") === "previous";
  const requested = csv(params.get("host"));
  const explicit = requested.length > 0;
  // With no host in the URL the first in-scope host is shown, and the page says so. A URL host is never replaced silently.
  const wantedAll = explicit ? requested : pickable.slice(0, 1).map((h) => h.host_id);
  const wanted = wantedAll.slice(0, MAX_HOSTS);

  // Hosts that are not in the loaded list (out of the search, or beyond the first page) are looked up directly.
  const lookupIds = wanted.filter((id) => !pickableIds.has(id));
  const lookups = useQueries({
    queries: lookupIds.map((id) => ({
      queryKey: ["host", id],
      queryFn: ({ signal }: { signal: AbortSignal }) => api.host(id, signal),
      refetchInterval: 15_000,
      retry: false,
    })),
  });
  const resolved: Resolved[] = wanted.map((id): Resolved => {
    const listed = pickable.find((h) => h.host_id === id);
    if (listed) return { id, state: "ready", host: listed };
    const i = lookupIds.indexOf(id);
    const q = lookups[i];
    if (!q || q.isPending) return { id, state: "pending" };
    if (q.isError || !q.data) return { id, state: "error", error: q?.error };
    if (!selected.includes(q.data.source)) return { id, state: "outside", source: q.data.source };
    return { id, state: "ready", host: q.data };
  });
  const drawn = resolved.filter((r): r is Extract<Resolved, { state: "ready" }> => r.state === "ready").map((r) => r.host);

  const wins = useMetricWindows(drawn.map((h) => h.host_id), metric.name, agg, range);
  const rangeMs = rangeDef.ms;

  // Assemble the chart and the statistics from the real responses only.
  const plot: Series[] = [];
  const rows: { key: string; host: Host; labels: Record<string, string>; points?: SeriesPoint[]; prev?: SeriesPoint[] }[] = [];
  const hostStatus = new Map<string, "pending" | "error" | "empty" | "ok">();
  let window: Pick<SeriesResponse, "start" | "end" | "step_seconds"> | undefined;
  drawn.forEach((h, i) => {
    const cur = wins[2 * i];
    const prev = wins[2 * i + 1]?.data;
    if (!cur || cur.isPending) return hostStatus.set(h.host_id, "pending");
    if (cur.isError && !cur.data) return hostStatus.set(h.host_id, "error");
    const d = cur.data!;
    window ??= d;
    const any = d.series.some((s) => s.points.some((p) => p.value != null));
    hostStatus.set(h.host_id, any ? "ok" : "empty");
    if (d.series.length === 0) rows.push({ key: `${h.host_id}:none`, host: h, labels: {} });
    for (const s of d.series) {
      const labels = { host: h.hostname, ...s.labels };
      plot.push({ ...s, labels });
      const match = prev?.series.find((p) => labelKey(p.labels) === labelKey(s.labels));
      if (compare && match) {
        plot.push({
          ...match,
          labels: { host: h.hostname, ...match.labels, period: PREVIOUS },
          points: match.points.map((p) => ({ ...p, t: new Date(Date.parse(p.t) + rangeMs).toISOString() })),
        });
      }
      rows.push({ key: `${h.host_id}:${labelKey(s.labels)}`, host: h, labels: s.labels, points: s.points, prev: match?.points });
    }
  });
  const anyPoints = plot.some((s) => s.points.some((p) => p.value != null));
  const setParam = (k: string, v: string, def?: string) =>
    setParams((p) => { const n = new URLSearchParams(p); if (v === def) n.delete(k); else n.set(k, v); return n; }, { replace: true });
  const setHosts = (ids: string[]) => setParams((p) => { const n = new URLSearchParams(p); if (ids.length) n.set("host", ids.join(",")); else n.delete("host"); return n; }, { replace: true });
  const toggleHost = (id: string, on: boolean) => {
    const next = on ? [...wanted, id] : wanted.filter((x) => x !== id);
    setHosts(next.slice(0, MAX_HOSTS));
  };
  const refreshAll = () => {
    wins.forEach((w) => w.refetch());
    lookups.forEach((l) => l.refetch());
    hostsQ.refetch();
  };
  const stepLabel = window ? `${agg} per ${window.step_seconds}s bucket` : `${agg} · bucket chosen by the server`;
  const scopeLabel = selected.map((s) => SOURCE_LABEL[s]).join(" + ") || "none";

  return (
    <>
      <PageHeader title="Metrics Explorer" subtitle={`One metric across up to ${MAX_HOSTS} hosts, from the stored observations`} />

      <section className="widget metrics-query" aria-label="Query">
        <div className="widget-body">
          <div className="toolbar">
            <label className="field">
              <span className="dim">Metric</span>
              <select value={metric.name} onChange={(e) => setParam("metric", e.target.value, METRICS[0]!.name)} aria-label="Metric">
                {METRICS.map((m) => <option key={m.name} value={m.name}>{m.label}</option>)}
              </select>
            </label>
            <label className="field">
              <span className="dim">Aggregation</span>
              <select value={agg} onChange={(e) => setParam("agg", e.target.value, "avg")} aria-label="Aggregation">
                {AGGS.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </label>
            <label className="field">
              <span className="dim">Compare</span>
              <select value={compare ? "previous" : "off"} onChange={(e) => setParam("compare", e.target.value === "previous" ? "previous" : "off", "off")} aria-label="Compare">
                <option value="off">Off</option>
                <option value="previous">Previous period</option>
              </select>
            </label>
            <button type="button" className="btn" onClick={refreshAll}>Refresh</button>
            <button type="button" className="btn" disabled title="Export is not built yet.">Export (not built)</button>
            <button type="button" className="btn" disabled title="The series API takes one metric and one host per request. Multiple queries are not built.">Add query (not built)</button>
            <button type="button" className="btn" disabled title="No forecast is produced for any metric.">Forecast (not available)</button>
          </div>

          <div className="metrics-hosts">
            <div className="toolbar">
              <label className="field">
                <span className="dim">Find host</span>
                <input type="search" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Hostname or host id" aria-label="Find host" />
              </label>
              <span className="dim small">
                Scope: {scopeLabel} (top bar) · last {rangeDef.label} (top bar) · {stepLabel}
              </span>
            </div>
            {hostsQ.isPending && <Loading label="Loading hosts" />}
            {hostsQ.isError && !hostsQ.data && <ErrorState error={hostsQ.error} onRetry={() => hostsQ.refetch()} />}
            {hostsQ.data && hostsQ.data.total > pickable.length && (
              <p className="dim small">Showing {pickable.length} of {hostsQ.data.total} hosts in scope. Use Find host to reach the others.</p>
            )}
            {hostsQ.data && pickable.length === 0 && (
              <EmptyState title="No hosts in scope" body="There is nothing to explore for the selected data sources. Widen the source scope in the top bar." />
            )}
            <fieldset className="metrics-picker">
              <legend className="dim small">Hosts drawn ({wanted.length} of up to {MAX_HOSTS})</legend>
              {pickable.map((h) => {
                const on = wanted.includes(h.host_id);
                return (
                  <label key={h.host_id} className="hf-row">
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={!on && wanted.length >= MAX_HOSTS}
                      title={!on && wanted.length >= MAX_HOSTS ? `Up to ${MAX_HOSTS} hosts at once, so each keeps its own colour.` : undefined}
                      onChange={(e) => toggleHost(h.host_id, e.target.checked)}
                    />
                    <span>{h.hostname}</span>
                    <SourceBadge source={h.source} />
                  </label>
                );
              })}
            </fieldset>
          </div>

          {truncated(requested) && <p className="banner notice" role="status">Only the first {MAX_HOSTS} selected hosts are drawn.</p>}
          {resolved.map((r) => {
            if (r.state === "outside") {
              return (
                <p key={r.id} className="banner notice" role="status">
                  {r.id} is {SOURCE_LABEL[r.source]}, which is outside the selected sources, so it is not drawn. Widen the source scope in the top bar to include it.
                </p>
              );
            }
            if (r.state === "error") {
              return <ErrorState key={r.id} error={r.error} />;
            }
            return null;
          })}
          {!explicit && pickable.length > 0 && (
            <p className="dim small">No host is selected in the URL, so the first in-scope host is shown. Tick hosts above to choose others.</p>
          )}
        </div>
      </section>

      <section className="widget" aria-label="Chart">
        <div className="widget-head">
          <h2>{metric.label}{drawn.length ? ` · ${drawn.map((h) => h.hostname).join(", ")}` : ""}</h2>
          <span className="dim mono small">last {rangeDef.label} · {stepLabel}{compare ? " · previous period overlaid" : ""}</span>
        </div>
        <div className="widget-body">
          {resolved.some((r) => r.state === "pending") && <Loading label="Checking the selected hosts" />}
          {drawn.length === 0 && resolved.length === 0 && pickable.length > 0 && <p className="dim">Select a host to draw its {metric.label.toLowerCase()}.</p>}
          {drawn.map((h) => {
            const st = hostStatus.get(h.host_id);
            if (st === "pending") return <Loading key={h.host_id} label={`Loading ${h.hostname}`} />;
            if (st === "error") {
              const i = drawn.indexOf(h);
              return <ErrorState key={h.host_id} error={wins[2 * i]?.error} onRetry={() => wins[2 * drawn.indexOf(h)]?.refetch()} />;
            }
            return null;
          })}
          {!anyPoints && drawn.some((h) => hostStatus.get(h.host_id) === "empty") && (
            <EmptyState
              title="No data in this window"
              body={drawn
                .filter((h) => hostStatus.get(h.host_id) === "empty")
                .map((h) => `${h.hostname} reported no ${metric.label.toLowerCase()} samples in the last ${rangeDef.label} (last seen ${timestamp(h.last_seen)}).${h.source === "replay" ? " Replayed history may lie outside the selected range." : ""}`)
                .join(" ") + " Try a longer range."}
            />
          )}
          {anyPoints && window && (
            <TimeSeriesChart
              series={plot}
              unit={metric.unit}
              start={window.start}
              end={window.end}
              label={`${metric.label} for ${drawn.map((h) => h.hostname).join(", ")}, ${agg}, last ${rangeDef.label}${compare ? ", previous period overlaid" : ""}`}
              height={260}
            />
          )}
          {metric.note && <p className="dim small">{metric.note} Gaps in a line mean no valid value (missing samples or a counter reset), not zero.</p>}
        </div>
      </section>

      {rows.length > 0 && (
        <section className="widget" aria-label="Series statistics">
          <div className="widget-head">
            <h2>Series</h2>
            <span className="dim mono small">{rangeDef.label} window · statistics from the valid {agg} buckets only</span>
          </div>
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Host</th><th>Source</th><th>Series</th>
                  <th className="num">Min</th><th className="num">Avg</th><th className="num">Max</th><th className="num">Last</th>
                  <th className="num">Previous window avg</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const s = statsOf(r.points);
                  const p = statsOf(r.prev);
                  const u = metric.unit;
                  const dash = (v?: number) => (v == null ? <span className="dim">{DASH}</span> : fmt(u, v));
                  return (
                    <tr key={r.key}>
                      <td>{r.host.hostname}</td>
                      <td><SourceBadge source={r.host.source} /></td>
                      <td className="small">
                        {Object.keys(r.labels).length ? Object.values(r.labels).join(" · ") : <span className="dim">{r.points ? "—" : "no data in this window"}</span>}
                      </td>
                      <td className="num mono">{dash(s?.min)}</td>
                      <td className="num mono">{dash(s?.avg)}</td>
                      <td className="num mono">{dash(s?.max)}</td>
                      <td className="num mono" title={s ? timestamp(s.lastAt) : undefined}>{dash(s?.last)}</td>
                      <td className="num mono">{dash(p?.avg)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}

function truncated(requested: string[]): boolean {
  return requested.length > MAX_HOSTS;
}
