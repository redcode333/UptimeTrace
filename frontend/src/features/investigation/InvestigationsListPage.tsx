import { Link, useSearchParams } from "react-router-dom";
import { useAlerts } from "../../api/hooks";
import type { Alert, AlertStatusFilter } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { HealthBadge } from "../../components/ui/Badges";
import { timestamp } from "../../lib/format";
import { conditionText } from "../alerts/AlertsPage";

/** The alert list endpoint returns at most this many rows per request, and has no offset. */
export const LIST_LIMIT = 200;
const STATUSES: { id: AlertStatusFilter; label: string }[] = [
  { id: "active", label: "Active" },
  { id: "resolved", label: "Resolved" },
  { id: "all", label: "All" },
];
const SORTS = {
  opened_desc: "Opened (newest)",
  opened_asc: "Opened (oldest)",
  severity: "Severity (critical first)",
  host: "Host (A–Z)",
} as const;
type Sort = keyof typeof SORTS;

const LIFECYCLE: Record<Alert["state"], string> = { open: "Open", acknowledged: "Acknowledged", resolved: "Resolved" };

export function lifecycleLabel(a: Alert): string {
  if (a.state !== "resolved") return LIFECYCLE[a.state];
  return a.resolution === "recovered" ? "Resolved: recovered" : a.resolution === "manual" ? "Resolved: by hand" : "Resolved";
}

/** Search matches the host, the condition and the id. Sorting is stable: ties fall back to the alert id. */
export function investigationRows(alerts: Alert[], search: string, sort: Sort): Alert[] {
  const needle = search.trim().toLowerCase();
  const rows = needle
    ? alerts.filter((a) => `${a.host_id} ${conditionText(a)} ${a.id}`.toLowerCase().includes(needle))
    : [...alerts];
  const rank = { critical: 0, warning: 1 } as Record<string, number>;
  rows.sort((x, y) => {
    if (sort === "severity") return (rank[x.severity] ?? 9) - (rank[y.severity] ?? 9) || y.opened_at.localeCompare(x.opened_at) || x.id.localeCompare(y.id);
    if (sort === "host") return x.host_id.localeCompare(y.host_id) || y.opened_at.localeCompare(x.opened_at) || x.id.localeCompare(y.id);
    const dir = sort === "opened_asc" ? 1 : -1;
    return dir * x.opened_at.localeCompare(y.opened_at) || x.id.localeCompare(y.id);
  });
  return rows;
}

/** Investigations are the alerts the platform raised: each alert has one investigation view with its evidence, lifecycle and record. */
export function InvestigationsListPage() {
  const [params, setParams] = useSearchParams();
  const status = (STATUSES.find((s) => s.id === params.get("status"))?.id ?? "active") as AlertStatusFilter;
  const sort: Sort = params.get("sort") && params.get("sort")! in SORTS ? (params.get("sort") as Sort) : "opened_desc";
  const search = params.get("q") ?? "";
  const q = useAlerts(status, LIST_LIMIT);
  const set = (k: string, v: string, def: string) =>
    setParams((p) => { const n = new URLSearchParams(p); if (v === def) n.delete(k); else n.set(k, v); return n; }, { replace: true });

  const loaded = q.data?.alerts ?? [];
  const total = q.data?.total ?? 0;
  const rows = investigationRows(loaded, search, sort);
  const partial = total > loaded.length;
  const statusLabel = STATUSES.find((s) => s.id === status)!.label.toLowerCase();

  return (
    <>
      <PageHeader title="Investigations" subtitle="Each alert the platform raised, with its evidence, lifecycle and record. Open one to see what it is based on." />
      <div className="toolbar" role="group" aria-label="Lifecycle status">
        {STATUSES.map((s) => (
          <button key={s.id} type="button" className="btn" aria-pressed={status === s.id} onClick={() => set("status", s.id, "active")}>{s.label}</button>
        ))}
        <label className="field">
          <span className="sr-only">Search investigations</span>
          <input type="search" value={search} placeholder="Search host, condition or id" onChange={(e) => set("q", e.target.value, "")} />
        </label>
        <label className="field">
          <span className="dim">Sort</span>
          <select aria-label="Sort investigations" value={sort} onChange={(e) => set("sort", e.target.value, "opened_desc")}>
            {(Object.keys(SORTS) as Sort[]).map((k) => <option key={k} value={k}>{SORTS[k]}</option>)}
          </select>
        </label>
      </div>

      {q.isPending && <Loading label="Loading investigations" />}
      {q.isError && !q.data && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <>
          <p className="dim small" role="status">
            {partial
              ? `Showing ${rows.length} of the ${loaded.length} most recent ${statusLabel} investigations loaded. The workspace holds ${total}; the list returns at most ${LIST_LIMIT} per request, so search and sort cover only the loaded rows.`
              : `${rows.length} of ${total} ${statusLabel} investigation${total === 1 ? "" : "s"}${search.trim() ? ` match "${search.trim()}"` : ""}.`}
          </p>
          {rows.length === 0 ? (
            <EmptyState
              title={search.trim() ? "No investigation matches" : `No ${statusLabel} investigations`}
              body={search.trim() ? "Clear the search, or widen the status filter." : "Nothing has been raised for this status in this workspace."}
            />
          ) : (
            <div className="table-wrap">
              <table className="tbl" aria-label="Investigations">
                <thead>
                  <tr><th>Investigation</th><th>Host</th><th>Trigger</th><th>Severity</th><th>Lifecycle</th><th>Opened</th><th>Last update</th></tr>
                </thead>
                <tbody>
                  {rows.map((a) => (
                    <tr key={a.id}>
                      <td>
                        <Link to={`/alerts/${encodeURIComponent(a.id)}`} className="mono small" aria-label={`Open investigation ${a.id.slice(0, 8)} for ${a.host_id}`}>{a.id.slice(0, 8)}</Link>
                      </td>
                      <td><Link to={`/hosts/${encodeURIComponent(a.host_id)}`}>{a.host_id}</Link></td>
                      <td className="small">{conditionText(a)}</td>
                      <td><HealthBadge status={a.severity as "critical" | "warning"} /></td>
                      <td className="small">{lifecycleLabel(a)}</td>
                      <td className="mono small" title={a.opened_at}>{timestamp(a.opened_at)}</td>
                      <td className="mono small" title={a.resolved_at ?? a.last_evaluated_at}>{timestamp(a.resolved_at ?? a.last_evaluated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </>
  );
}
