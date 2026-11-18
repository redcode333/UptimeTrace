import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useHostList } from "../../api/hooks";
import type { Host, HostListParams } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { StaleBanner } from "../../components/ui/Banners";
import { HealthBadge, SourceBadge, healthLabel } from "../../components/ui/Badges";
import { UtilCell } from "../../components/ui/UtilCell";
import { ago, bytesPerSecond, timestamp } from "../../lib/format";
import { applyHostsView, HEALTHS, PAGE_SIZE, parseHostsView } from "../../lib/hostsQuery";
import { scopeFilter, useScope } from "../../lib/scope";
import { globalSearch } from "../../lib/timeRange";

const REPORTING: Record<Host["reporting_status"], string> = { reporting: "Reporting", silent: "Silent", replay: "Replay (not live)" };

export function HostsPage() {
  const [params, setParams] = useSearchParams();
  const { selected } = useScope();
  const view = parseHostsView(params);
  const [draft, setDraft] = useState(view.search);
  useEffect(() => setDraft(view.search), [view.search]);
  // Debounce typing into the URL so each keystroke does not become a request.
  useEffect(() => {
    if (draft.trim() === view.search.trim()) return;
    const t = setTimeout(() => setParams((p) => applyHostsView(p, { search: draft }), { replace: true }), 300);
    return () => clearTimeout(t);
  }, [draft, view.search, setParams]);

  const query: HostListParams = {
    search: view.search.trim() || undefined,
    source: scopeFilter(selected),
    health: view.health.length ? view.health : undefined,
    sort: view.sort,
    order: view.order,
    page: view.page,
    page_size: PAGE_SIZE,
  };
  const q = useHostList(query);
  const update = (patch: Parameters<typeof applyHostsView>[1]) => setParams((p) => applyHostsView(p, patch), { replace: false });
  const data = q.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const carry = globalSearch(params);
  const filtered = view.health.length > 0 || view.search.trim() !== "";

  return (
    <>
      <PageHeader
        title="Hosts"
        subtitle={data ? `${data.total} host${data.total === 1 ? "" : "s"} match the current scope and filters` : "Host inventory"}
      />
      <div className="toolbar">
        <label className="field">
          <span className="sr-only">Search hosts</span>
          <input type="search" placeholder="Search hostname or host id" value={draft} onChange={(e) => setDraft(e.target.value)} />
        </label>
        <div className="seg" role="group" aria-label="Health filter">
          {HEALTHS.map((h) => {
            const on = view.health.includes(h);
            return (
              <button key={h} type="button" className={on ? "on" : ""} aria-pressed={on} onClick={() => update({ health: on ? view.health.filter((x) => x !== h) : [...view.health, h] })}>
                {healthLabel(h)} <b className="mono">{data ? data.facets.health[h] : "–"}</b>
              </button>
            );
          })}
        </div>
        <label className="field">
          <span className="dim">Sort</span>
          <select
            value={`${view.sort}:${view.order}`}
            onChange={(e) => { const [sort, order] = e.target.value.split(":") as [typeof view.sort, typeof view.order]; update({ sort, order }); }}
          >
            <option value="last_seen:desc">Last seen (newest)</option>
            <option value="last_seen:asc">Last seen (oldest)</option>
            <option value="hostname:asc">Hostname (A–Z)</option>
            <option value="hostname:desc">Hostname (Z–A)</option>
          </select>
        </label>
        {filtered && <button type="button" className="btn" onClick={() => { setDraft(""); update({ search: "", health: [] }); }}>Clear filters</button>}
      </div>

      {q.isPending && <Loading label="Loading hosts" />}
      {q.isError && !data && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {data && (
        <>
          <StaleBanner failed={q.isError} updatedAt={q.dataUpdatedAt} onRetry={() => q.refetch()} />
          {data.total === 0 ? (
            <EmptyState
              title={filtered || selected.length < 3 ? "No hosts match" : "No hosts have reported yet"}
              body={filtered || selected.length < 3 ? "Nothing matches the selected data sources and filters. Widen the source scope or clear the filters." : "Hosts appear here after an agent sends its first telemetry envelope."}
            />
          ) : (
            <div className={`widget table-wrap${q.isFetching ? " is-fetching" : ""}`}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Host</th><th>Source</th><th>Health</th><th>Reporting</th><th>Agent</th>
                    <th className="num">CPU</th><th className="num">Memory</th><th className="num">Disk (worst)</th>
                    <th className="num">Net rx / tx</th><th>First seen</th><th>Last seen</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((h) => (
                    <tr key={h.host_id}>
                      <td><Link className="hostlink" to={`/hosts/${encodeURIComponent(h.host_id)}${carry}`}>{h.hostname}</Link>{h.hostname !== h.host_id && <div className="dim mono small">{h.host_id}</div>}</td>
                      <td><SourceBadge source={h.source} /></td>
                      <td><HealthBadge status={h.health.status} />{h.health.reasons[0] && <div className="dim small">{h.health.reasons.length} threshold{h.health.reasons.length > 1 ? "s" : ""} crossed</div>}</td>
                      <td>{REPORTING[h.reporting_status]}</td>
                      <td className="mono small">{h.agent_id}</td>
                      <td className="num"><UtilCell value={h.latest.cpu_percent} /></td>
                      <td className="num"><UtilCell value={h.latest.memory_percent} /></td>
                      <td className="num"><UtilCell value={h.latest.disk_percent_max} warn={90} />{h.latest.disk_mount_max && <div className="dim mono small">{h.latest.disk_mount_max}</div>}</td>
                      <td className="num mono small">{bytesPerSecond(h.latest.network_rx_bytes_per_second)} / {bytesPerSecond(h.latest.network_tx_bytes_per_second)}</td>
                      <td className="mono small" title={h.first_seen}>{timestamp(h.first_seen)}</td>
                      <td className="small" title={h.last_seen}><span className="mono">{timestamp(h.last_seen)}</span><div className="dim">{ago(h.last_seen_age_seconds)}</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data.total > 0 && (
            <nav className="pager" aria-label="Pagination">
              <span className="dim">Page {data.page} of {pages} · showing {(data.page - 1) * data.page_size + 1}–{Math.min(data.total, data.page * data.page_size)} of {data.total}</span>
              <button type="button" className="btn" disabled={data.page <= 1} onClick={() => update({ page: data.page - 1 })}>Previous</button>
              <button type="button" className="btn" disabled={data.page >= pages} onClick={() => update({ page: data.page + 1 })}>Next</button>
            </nav>
          )}
        </>
      )}
    </>
  );
}
