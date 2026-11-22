import { Link, useSearchParams } from "react-router-dom";
import { useAlerts, useHostList } from "../../api/hooks";
import type { Alert, AlertStatusFilter, Host, HealthStatus, HostList, Source, Thresholds } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { HealthBadge, SourceBadge, healthLabel } from "../../components/ui/Badges";
import { ago, timestamp } from "../../lib/format";
import { alertsInScope, indexHosts } from "../../lib/commandCenter";
import { HEALTHS } from "../../lib/hostsQuery";
import { SOURCE_LABEL, scopeFilter, useScope } from "../../lib/scope";
import { conditionText } from "../alerts/AlertsPage";
import { lifecycleLabel } from "../investigation/InvestigationsListPage";

/** Host health: the rules in backend/app/monitoring/health.py, in the words the product uses. */
export const HEALTH_MEANING: Record<HealthStatus, string> = {
  healthy: "Telemetry is fresh, and every judged metric is below its warning limit.",
  warning: "Telemetry is fresh, and at least one judged metric is at or above its warning limit.",
  critical: "Telemetry is fresh, and at least one judged metric is at or above its critical limit.",
  stale: "Telemetry is later than the stale limit but not yet past the offline limit. The last values are not judged.",
  offline: "No telemetry for longer than the offline limit. The last values are not judged.",
  unknown: "There is no fresh CPU, memory or disk sample to judge, so health cannot be decided. A missing value is never treated as zero.",
};

const ALERT_STATUSES: { id: AlertStatusFilter; label: string }[] = [
  { id: "active", label: "Active" },
  { id: "resolved", label: "Resolved" },
  { id: "all", label: "All" },
];
const LIST_PAGE = 50;

function isHealth(v: string | null): v is HealthStatus {
  return (HEALTHS as readonly string[]).includes(v ?? "");
}

export function StatesPage() {
  const [params, setParams] = useSearchParams();
  const { selected } = useScope();
  const source = scopeFilter(selected);
  const health: HealthStatus = isHealth(params.get("health")) ? (params.get("health") as HealthStatus) : "critical";
  const alertStatus: AlertStatusFilter = (ALERT_STATUSES.find((s) => s.id === params.get("status"))?.id ?? "active") as AlertStatusFilter;

  const counts = useHostList({ source, page_size: 1 }, { refetchInterval: 15_000 });
  const listed = useHostList({ source, health: [health], sort: "attention", page_size: LIST_PAGE }, { refetchInterval: 15_000 });
  // Unscoped on purpose: the index must know every loaded host's source, so alerts can be placed in or out of the selection.
  const index = useHostList({ page_size: 200, sort: "attention" }, { refetchInterval: 15_000 });
  const active = useAlerts("active", 200);
  const resolved = useAlerts("resolved", 1);
  const listedAlerts = useAlerts(alertStatus, 200);

  const set = (k: string, v: string) => setParams((p) => { const n = new URLSearchParams(p); n.set(k, v); return n; }, { replace: true });

  return (
    <section className="sg" aria-label="Operational states">
      <PageHeader
        title="Operational states"
        subtitle="Two separate state machines: what each host is in (health), and what each alert is in (lifecycle). Counts cover this workspace and the selected sources."
      />
      <p className="dim small">
        Selected sources: {selected.map((s) => SOURCE_LABEL[s]).join(" + ") || "none"} (top bar). LIVE, SYNTHETIC and REPLAY hosts are counted separately; a synthetic or replay host is never presented as live telemetry.
      </p>

      <HostHealthSection counts={counts} listed={listed} health={health} onPick={(h) => set("health", h)} source={source} />
      <AlertLifecycleSection
        active={active}
        resolved={resolved}
        listed={listedAlerts}
        status={alertStatus}
        onPick={(s) => set("status", s)}
        index={index}
        selected={selected}
      />
      <TransitionsSection thresholds={counts.data?.thresholds} />
    </section>
  );
}

function HostHealthSection({
  counts, listed, health, onPick, source,
}: {
  counts: ReturnType<typeof useHostList>;
  listed: ReturnType<typeof useHostList>;
  health: HealthStatus;
  onPick: (h: HealthStatus) => void;
  source: Source[] | undefined;
}) {
  const facets = counts.data?.facets;
  const sourceTotals = facets?.source;
  return (
    <section className="widget" aria-labelledby="states-host">
      <div className="widget-head">
        <h2 id="states-host">Host health</h2>
        <span className="dim mono small">
          {sourceTotals ? `LIVE ${sourceTotals.live} · SYNTHETIC ${sourceTotals.synthetic} · REPLAY ${sourceTotals.replay} in workspace` : ""}
        </span>
      </div>
      <div className="widget-body">
        {counts.isPending && <Loading label="Counting hosts by health" />}
        {counts.isError && !counts.data && (
          <ErrorState error={counts.error} onRetry={() => counts.refetch()} />
        )}
        {counts.data && (
          <>
            <div className="table-wrap"><table className="tbl" aria-label="Host health counts">
              <thead>
                <tr><th>State</th><th className="num">Hosts</th><th>What it means</th><th><span className="sr-only">Actions</span></th></tr>
              </thead>
              <tbody>
                {HEALTHS.map((h) => (
                  <tr key={h} aria-selected={h === health}>
                    <td><HealthBadge status={h} /></td>
                    <td className="num mono">{facets ? facets.health[h] : "—"}</td>
                    <td className="small">{HEALTH_MEANING[h]}</td>
                    <td className="small">
                      <div className="row-actions">
                        <button type="button" className="btn" aria-pressed={h === health} onClick={() => onPick(h)}>
                          {h === health ? "Showing" : "Show hosts"}
                        </button>
                        <Link to={`/hosts?health=${h}`}>Open in Hosts</Link>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table></div>
            <p className="dim small">
              Counts cover hosts that have sent telemetry. A registered host that has never reported is not counted here; see Agents.
              {" "}Counts follow the selected sources only; each state's list is the server's own.
            </p>
          </>
        )}
      </div>
      <div className="widget-body">
        <h3 className="small">{healthLabel(health)} hosts in this scope</h3>
        {listed.isPending && <Loading label="Loading hosts" />}
        {listed.isError && !listed.data && <ErrorState error={listed.error} onRetry={() => listed.refetch()} />}
        {listed.data && <HostRows data={listed.data} health={health} source={source} />}
      </div>
    </section>
  );
}

function HostRows({ data, health, source }: { data: HostList; health: HealthStatus; source: Source[] | undefined }) {
  if (data.total === 0) {
    return (
      <EmptyState
        title={`No ${healthLabel(health).toLowerCase()} hosts in this scope`}
        body={`Nothing is ${healthLabel(health).toLowerCase()} for ${source ? "the selected sources" : "this workspace"}. ${HEALTH_MEANING[health]}`}
      />
    );
  }
  return (
    <>
      {data.total > data.items.length && (
        <p className="dim small" role="status">Showing {data.items.length} of {data.total}. The rest are in Hosts with the same filter.</p>
      )}
      <div className="table-wrap"><table className="tbl" aria-label={`${healthLabel(health)} hosts`}>
        <thead><tr><th>Host</th><th>Source</th><th>Reporting</th><th>Last telemetry</th><th>Why</th></tr></thead>
        <tbody>
          {data.items.map((h: Host) => (
            <tr key={h.host_id}>
              <td><Link to={`/hosts/${encodeURIComponent(h.host_id)}`}>{h.hostname}</Link></td>
              <td><SourceBadge source={h.source} /></td>
              <td className="small">{h.reporting_status}</td>
              <td className="small mono" title={h.last_seen}>{timestamp(h.last_seen)} <span className="dim">({ago(h.last_seen_age_seconds)})</span></td>
              <td className="small">{h.health.reasons.length ? h.health.reasons.map((r) => `${r.metric.replace("system.", "")} ${r.value.toFixed(1)}%`).join(", ") : HEALTH_MEANING[h.health.status]}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </>
  );
}

function AlertLifecycleSection({
  active, resolved, listed, status, onPick, index, selected,
}: {
  active: ReturnType<typeof useAlerts>;
  resolved: ReturnType<typeof useAlerts>;
  listed: ReturnType<typeof useAlerts>;
  status: AlertStatusFilter;
  onPick: (s: AlertStatusFilter) => void;
  index: ReturnType<typeof useHostList>;
  selected: Source[];
}) {
  const hostIndex = index.data ? indexHosts(index.data.items) : null;
  const scoped = listed.data ? alertsInScope(listed.data.alerts, selected, hostIndex) : null;
  const rows: Alert[] = scoped?.alerts ?? [];
  const loaded = active.data?.alerts ?? [];
  const activeScoped = alertsInScope(loaded, selected, hostIndex);
  const breakdown = {
    open: activeScoped.alerts.filter((a) => a.state === "open").length,
    acknowledged: activeScoped.alerts.filter((a) => a.state === "acknowledged").length,
    critical: activeScoped.alerts.filter((a) => a.severity === "critical").length,
    warning: activeScoped.alerts.filter((a) => a.severity === "warning").length,
  };
  const activeTotal = active.data?.total;
  const partial = active.data ? active.data.total > loaded.length : false;

  return (
    <section className="widget" aria-labelledby="states-alert">
      <div className="widget-head">
        <h2 id="states-alert">Alert lifecycle</h2>
        <span className="dim mono small">Alerts are one per condition on a host; each has an investigation</span>
      </div>
      <div className="widget-body">
        {active.isPending && <Loading label="Counting active alerts" />}
        {active.isError && !active.data && <ErrorState error={active.error} onRetry={() => active.refetch()} />}
        {active.data && (
          <div className="table-wrap"><table className="tbl" aria-label="Alert lifecycle counts">
            <thead><tr><th>Lifecycle state</th><th className="num">Alerts</th><th>Meaning</th></tr></thead>
            <tbody>
              <tr><td>Open</td><td className="num mono">{partial ? `${breakdown.open} of loaded` : breakdown.open}</td><td className="small">Raised and not yet acknowledged. Severity is warning or critical.</td></tr>
              <tr><td>Acknowledged</td><td className="num mono">{partial ? `${breakdown.acknowledged} of loaded` : breakdown.acknowledged}</td><td className="small">Someone has taken ownership. The condition is still live until it clears.</td></tr>
              <tr><td>Active, total</td><td className="num mono">{activeTotal}</td><td className="small">Open or acknowledged, in the whole workspace.</td></tr>
              <tr><td>Resolved, total</td><td className="num mono">{resolved.data ? resolved.data.total : resolved.isError ? "unavailable" : "—"}</td><td className="small">Closed by recovery (telemetry cleared the condition) or by hand.</td></tr>
            </tbody>
          </table></div>
        )}
        {partial && (
          <p className="dim small" role="status">
            Open and acknowledged counts are for the {loaded.length} most recent active alerts loaded; the workspace holds {activeTotal}. The total is exact.
          </p>
        )}
        {active.data && scoped && scoped.notLoaded > 0 && (
          <p className="dim small">{scoped.notLoaded} loaded alert{scoped.notLoaded === 1 ? "" : "s"} belong to hosts that are not in the loaded host list, so they cannot be placed in the selected sources and are not counted.</p>
        )}
        <div className="row-actions" role="group" aria-label="Alert status filter">
          {ALERT_STATUSES.map((s) => (
            <button key={s.id} type="button" className="btn" aria-pressed={status === s.id} onClick={() => onPick(s.id)}>{s.label}</button>
          ))}
        </div>
      </div>
      <div className="widget-body">
        {listed.isPending && <Loading label="Loading alerts" />}
        {listed.isError && !listed.data && <ErrorState error={listed.error} onRetry={() => listed.refetch()} />}
        {listed.data && rows.length === 0 && (
          <EmptyState
            title={`No ${status === "all" ? "" : `${status} `}alerts in this scope`}
            body={status === "active" ? "No open or acknowledged alert is on the selected sources." : "No alert with this status is on the selected sources."}
          />
        )}
        {listed.data && rows.length > 0 && (
          <div className="table-wrap"><table className="tbl" aria-label="Alerts in this scope">
            <thead><tr><th>Host</th><th>Source</th><th>Condition</th><th>Severity</th><th>Lifecycle</th><th>Opened</th><th><span className="sr-only">Investigation</span></th></tr></thead>
            <tbody>
              {rows.map((a) => {
                const host = hostIndex?.get(a.host_id);
                return (
                  <tr key={a.id}>
                    <td><Link to={`/hosts/${encodeURIComponent(a.host_id)}`}>{host?.hostname ?? a.host_id}</Link></td>
                    <td>{host ? <SourceBadge source={host.source} /> : <span className="dim small">not loaded</span>}</td>
                    <td className="small">{conditionText(a)}</td>
                    <td><HealthBadge status={a.severity as "critical" | "warning"} /></td>
                    <td className="small">{lifecycleLabel(a)}</td>
                    <td className="mono small">{timestamp(a.opened_at)}</td>
                    <td><Link to={`/alerts/${encodeURIComponent(a.id)}`}>Open investigation</Link></td>
                  </tr>
                );
              })}
            </tbody>
          </table></div>
        )}
        {listed.data && rows.length > 0 && listed.data.alerts.length >= 200 && (
          <p className="dim small">The list holds at most the 200 most recent alerts for this status; older ones are in Investigations.</p>
        )}
      </div>
    </section>
  );
}

function TransitionsSection({ thresholds }: { thresholds: Thresholds | undefined }) {
  const rules = thresholds?.rules ?? {};
  const cpu = rules["system.cpu.utilization"];
  const mem = rules["system.memory.utilization"];
  const disk = rules["system.disk.utilization"];
  const limit = (r: { warning: number; critical: number } | undefined) => (r ? `warning at ${r.warning}%, critical at ${r.critical}%` : "not configured");
  return (
    <section className="widget" aria-labelledby="states-rules">
      <div className="widget-head"><h2 id="states-rules">How states change</h2><span className="dim mono small">the rules in force, not a model</span></div>
      <div className="widget-body">
        <h3 className="small">Host health</h3>
        {thresholds ? (
          <ol className="plain">
            <li>Telemetry older than {thresholds.host_stale_after_seconds} s makes a host <b>stale</b>; older than {thresholds.offline_after_seconds} s makes it <b>offline</b>.</li>
            <li>While telemetry is fresh, the host is <b>unknown</b> until a fresh CPU, memory or disk sample exists.</li>
            <li>Then the thresholds decide: <b>critical</b> if any metric reaches its critical limit, <b>warning</b> at its warning limit, otherwise <b>healthy</b>.</li>
            <li>Limits: CPU {limit(cpu)}; memory {limit(mem)}; disk {limit(disk)}.</li>
            <li>Replay hosts replay stored history, so silence is their normal end: they are never stale or offline by the clock.</li>
          </ol>
        ) : (
          <p className="dim small">The configured limits could not be read, so the rules are not shown. Nothing is judged against an assumed limit.</p>
        )}
        <h3 className="small">Alert lifecycle</h3>
        <ol className="plain">
          <li>A condition that is breached opens one alert, in the open state.</li>
          <li>While it is live, its severity can move between warning and critical; each change is recorded in its timeline.</li>
          <li>An operator can acknowledge it. Acknowledged alerts stay live until the condition clears.</li>
          <li>When telemetry shows the condition cleared, the alert resolves by recovery. An operator can also resolve it by hand; if the breach is still there, a new alert opens at the next evaluation.</li>
          <li>A host that goes offline opens an offline alert, which resolves when the host reports again.</li>
        </ol>
        <p className="dim small">
          A host's health and an alert's lifecycle are separate state machines. Read one without assuming the other.
        </p>
      </div>
    </section>
  );
}
