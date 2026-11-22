import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useHostList, useInfrastructure } from "../../api/hooks";
import type { GroupRef, Host, HostListParams, Inventory } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { StaleBanner } from "../../components/ui/Banners";
import { HealthBadge, SourceBadge, healthLabel } from "../../components/ui/Badges";
import { UtilCell } from "../../components/ui/UtilCell";
import { ago, bytesPerSecond, timestamp } from "../../lib/format";
import { CONNECTION_LABEL } from "../../lib/connection";
import { applyHostsView, GROUP_KEYS, type GroupKey, HEALTHS, type HostsView, PAGE_SIZE, parseHostsView } from "../../lib/hostsQuery";
import { scopeFilter, useScope } from "../../lib/scope";
import { globalSearch } from "../../lib/timeRange";

const REPORTING: Record<Host["reporting_status"], string> = {
  reporting: CONNECTION_LABEL.connected, stale: CONNECTION_LABEL.stale, silent: CONNECTION_LABEL.offline, replay: CONNECTION_LABEL.replay,
};
const GROUP_LABEL: Record<GroupKey, string> = { environment: "Environment", region: "Region", cluster: "Cluster", service: "Service" };
const STATE_COLOUR: Record<string, string> = {
  healthy: "#1f8f73", warning: "#e0951f", critical: "#e5483a", stale: "#7f98ad", offline: "#4b5b6b", unknown: "#2e3a46",
};

/** Every registry group that can be filtered on, flattened from the inventory tree (regions and clusters can sit at the top level too). */
export function groupOptions(inv: Inventory): Record<GroupKey, GroupRef[]> {
  const envs = new Map<string, string>();
  const regions = new Map<string, string>();
  const clusters = new Map<string, string>();
  const services = new Map<string, string>();
  for (const e of inv.environments) {
    envs.set(e.id, e.name);
    for (const r of e.regions) {
      regions.set(r.id, r.name);
      for (const c of r.clusters) clusters.set(c.id, c.name);
    }
  }
  for (const r of inv.unassigned_regions) {
    regions.set(r.id, r.name);
    for (const c of r.clusters) clusters.set(c.id, c.name);
  }
  for (const c of inv.unassigned_clusters) clusters.set(c.id, c.name);
  for (const s of inv.services) services.set(s.id, s.name);
  const list = (m: Map<string, string>): GroupRef[] => [...m].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
  return { environment: list(envs), region: list(regions), cluster: list(clusters), service: list(services) };
}

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

  const groupParam = (k: GroupKey) => (view[k].length ? view[k] : undefined);
  const query: HostListParams = {
    search: view.search.trim() || undefined,
    source: scopeFilter(selected),
    health: view.health.length ? view.health : undefined,
    environment: groupParam("environment"),
    region: groupParam("region"),
    cluster: groupParam("cluster"),
    service: groupParam("service"),
    sort: view.sort,
    order: view.order,
    page: view.page,
    page_size: PAGE_SIZE,
  };
  const q = useHostList(query);
  const inventory = useInfrastructure();
  const update = (patch: Partial<HostsView>) => setParams((p) => applyHostsView(p, patch), { replace: false });
  const data = q.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const carry = globalSearch(params);
  // Host links return here: this list's URL travels as `from`, so Back and "All hosts" keep the filters.
  const here = `/hosts${params.toString() ? `?${params.toString()}` : ""}`;
  const hostHref = (id: string) => `/hosts/${encodeURIComponent(id)}${carry}${carry ? "&" : "?"}from=${encodeURIComponent(here)}`;
  const groupsSet = GROUP_KEYS.some((k) => view[k].length > 0);
  const filtered = view.health.length > 0 || view.search.trim() !== "" || groupsSet;
  const options = inventory.data ? groupOptions(inventory.data) : null;
  const toggleHealth = (h: string, on: boolean) =>
    update({ health: on ? [...view.health, h as HostsView["health"][number]] : view.health.filter((x) => x !== h) });
  const toggleGroup = (k: GroupKey, id: string, on: boolean) => update({ [k]: on ? [...view[k], id] : view[k].filter((x) => x !== id) });

  return (
    <>
      <PageHeader
        title="Hosts"
        subtitle={data ? `${data.total} host${data.total === 1 ? "" : "s"} match the current scope and filters` : "Host inventory"}
        actions={<><Link className="btn" to="/infrastructure">Infrastructure</Link> <Link className="btn" to="/onboarding">Register a host</Link></>}
      />
      <div className="toolbar">
        <label className="field">
          <span className="sr-only">Search hosts</span>
          <input type="search" placeholder="Search hostname or host id" value={draft} onChange={(e) => setDraft(e.target.value)} />
        </label>
        <label className="field">
          <span className="dim">Sort</span>
          <select
            aria-label="Sort"
            value={`${view.sort}:${view.order}`}
            onChange={(e) => { const [sort, order] = e.target.value.split(":") as [HostsView["sort"], HostsView["order"]]; update({ sort, order }); }}
          >
            <option value="attention:asc">Attention (most severe first)</option>
            <option value="last_seen:desc">Last seen (newest)</option>
            <option value="last_seen:asc">Last seen (oldest)</option>
            <option value="hostname:asc">Hostname (A–Z)</option>
            <option value="hostname:desc">Hostname (Z–A)</option>
          </select>
        </label>
        {filtered && (
          <button type="button" className="btn" onClick={() => { setDraft(""); update({ search: "", health: [], environment: [], region: [], cluster: [], service: [] }); }}>
            Clear filters
          </button>
        )}
      </div>

      <div className="hosts-board">
        <aside className="hosts-facets" aria-label="Filters">
          <p className="dim small">Filters narrow the hosts in the selected data sources. The source selector in the header is the only source control.</p>
          <fieldset className="hf-group">
            <legend>State</legend>
            {HEALTHS.map((h) => (
              <label key={h} className="hf-row">
                <input type="checkbox" checked={view.health.includes(h)} onChange={(e) => toggleHealth(h, e.target.checked)} />
                <span>{healthLabel(h)}</span>
                <b className="mono">{data ? data.facets.health[h] : "–"}</b>
              </label>
            ))}
          </fieldset>
          {GROUP_KEYS.map((key) => (
            <fieldset key={key} className="hf-group">
              <legend>{GROUP_LABEL[key]}</legend>
              {inventory.isPending && <p className="dim small">Loading…</p>}
              {inventory.isError && !inventory.data && (
                <p className="small">
                  Unavailable: the infrastructure list did not load.{" "}
                  <button type="button" className="btn" onClick={() => inventory.refetch()}>Retry</button>
                </p>
              )}
              {options && options[key].length === 0 && <p className="dim small">None registered yet.</p>}
              {options && options[key].map((o) => (
                <label key={o.id} className="hf-row">
                  <input type="checkbox" checked={view[key].includes(o.id)} onChange={(e) => toggleGroup(key, o.id, e.target.checked)} />
                  <span>{o.name}</span>
                </label>
              ))}
            </fieldset>
          ))}
        </aside>

        <div className="hosts-main">
          {data && (
            <div className="hosts-strip" aria-label="Hosts by state">
              <div className="hosts-strip-bar">
                {HEALTHS.filter((h) => data.facets.health[h] > 0).map((h) => (
                  <i
                    key={h}
                    title={`${healthLabel(h)} ${data.facets.health[h]}`}
                    style={{ width: `${(data.facets.health[h] / Math.max(1, data.total)) * 100}%`, background: STATE_COLOUR[h] }}
                  />
                ))}
              </div>
              <p className="dim small">
                {HEALTHS.map((h) => `${healthLabel(h)} ${data.facets.health[h]}`).join(" · ")}. Counts follow the source and registry filters, not the state filter.
              </p>
            </div>
          )}

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
                        <th>Environment · region</th><th>Cluster</th><th>Services</th>
                        <th className="num">CPU</th><th className="num">Memory</th><th className="num">Disk (worst)</th>
                        <th className="num">Net rx / tx</th><th>First seen</th><th>Last seen</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((h) => (
                        <tr key={h.host_id}>
                          <td><Link className="hostlink" to={hostHref(h.host_id)}>{h.hostname}</Link>{h.hostname !== h.host_id && <div className="dim mono small">{h.host_id}</div>}</td>
                          <td><SourceBadge source={h.source} /></td>
                          <td><HealthBadge status={h.health.status} />{h.health.reasons[0] && <div className="dim small">{h.health.reasons.length} threshold{h.health.reasons.length > 1 ? "s" : ""} crossed</div>}</td>
                          <td>{REPORTING[h.reporting_status]}</td>
                          <td className="mono small">{h.agent_id}</td>
                          <td className="small">
                            {h.environment || h.region
                              ? <Link to="/infrastructure">{[h.environment?.name, h.region?.name].filter(Boolean).join(" · ")}</Link>
                              : <span className="dim">Unplaced</span>}
                          </td>
                          <td className="small">{h.cluster ? <Link to="/infrastructure">{h.cluster.name}</Link> : <span className="dim">—</span>}</td>
                          <td className="small">
                            {h.services.length
                              ? h.services.map((s, i) => <span key={s.id}>{i > 0 && ", "}<Link to="/infrastructure">{s.name}</Link></span>)
                              : <span className="dim">—</span>}
                          </td>
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
        </div>
      </div>
    </>
  );
}
