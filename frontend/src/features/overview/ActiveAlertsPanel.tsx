import { Link } from "react-router-dom";
import type { UseQueryResult } from "@tanstack/react-query";
import { useAlertMutations } from "../../api/hooks";
import type { Alert, AlertList, Host, Source } from "../../api/types";
import { ErrorState, Loading } from "../../components/states/States";
import { PartialBanner, StaleBanner } from "../../components/ui/Banners";
import { HealthBadge } from "../../components/ui/Badges";
import { ago } from "../../lib/format";
import { summarizeAlerts } from "../../lib/commandCenter";
import { conditionText } from "../alerts/AlertsPage";

export interface ActiveAlertsPanelProps {
  query: UseQueryResult<AlertList>;
  /** Host index from the all-sources host list. Null until that list has loaded (or when a subset is not selected). */
  hostIndex: Map<string, Host> | null;
  /** The all-sources host list is complete (its total fits in the rows returned). */
  hostsComplete: boolean;
  selected: Source[];
  carry: string;
  now: number;
}

const secondsSince = (iso: string, now: number) => Math.max(0, (now - Date.parse(iso)) / 1000);

/** CC-5: active alerts for the selected scope, with exact-or-partial counts (docs/FRONTEND_ARCHITECTURE.md section 6.10). */
export function ActiveAlertsPanel({ query, hostIndex, hostsComplete, selected, carry, now }: ActiveAlertsPanelProps) {
  const m = useAlertMutations();
  return (
    <section className="widget" aria-label="Active alerts">
      <div className="widget-head">
        <h2>Active alerts</h2>
        {query.data && <span className="dim mono">{query.data.total} active · all sources</span>}
      </div>
      <div className="widget-body">
        {query.isPending && <Loading label="Loading active alerts" />}
        {query.isError && !query.data && (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        )}
        {query.data && <AlertsBody data={query.data} isStale={query.isError} onRetry={() => query.refetch()} updatedAt={query.dataUpdatedAt}
          hostIndex={hostIndex} hostsComplete={hostsComplete} selected={selected} carry={carry} now={now}
          ack={(id) => m.acknowledge.mutate(id)} ackPending={m.acknowledge.isPending} ackFailed={m.acknowledge.isError} />}
      </div>
    </section>
  );
}

function AlertsBody(p: {
  data: AlertList; isStale: boolean; onRetry: () => void; updatedAt: number;
  hostIndex: Map<string, Host> | null; hostsComplete: boolean; selected: Source[]; carry: string; now: number;
  ack: (id: string) => void; ackPending: boolean; ackFailed: boolean;
}) {
  const s = summarizeAlerts(p.data.alerts, p.data.total, p.selected, p.hostIndex, p.hostsComplete);
  const exact = s.scopeExact;
  // Not exact: every scope-filtered number is a count over the loaded alerts, so it is labelled as such.
  const tag = exact ? "" : " (loaded)";
  return (
    <>
      <StaleBanner failed={p.isStale} updatedAt={p.updatedAt} onRetry={p.onRetry} />
      {s.alertsCapped && (
        <PartialBanner>Partial: {s.loaded} of {s.totalActive} active alerts loaded. Counts below cover only the loaded alerts.</PartialBanner>
      )}
      {!s.alertsCapped && s.joinNeeded && !p.hostsComplete && (
        <PartialBanner>The host list for this scope is capped, so alerts cannot all be matched to a source.</PartialBanner>
      )}
      {p.ackFailed && <p className="banner warn" role="alert">Could not acknowledge the alert. Nothing was changed.</p>}

      {s.totalActive === 0 ? (
        <p className="dim">No active alerts in any source.</p>
      ) : (
        <>
          <div className="alert-counts" aria-label="Alert counts">
            <span className="small">{exact ? "In scope" : "Not exact for this scope"}: <b className="mono">{exact ? s.inScope.length : `${s.inScope.length} of ${s.totalActive} loaded`}</b></span>
            <span className="small">Critical{tag} <b className="mono">{s.bySeverity.critical}</b></span>
            <span className="small">Warning{tag} <b className="mono">{s.bySeverity.warning}</b></span>
            <span className="small">Threshold{tag} <b className="mono">{s.byRule.threshold}</b></span>
            <span className="small">Host offline{tag} <b className="mono">{s.byRule.host_offline}</b></span>
          </div>
          {!exact && <p className="dim small">Severity and rule counts cover the loaded alerts only; they are not totals for this scope.</p>}
          {s.joinNeeded && s.notLoaded > 0 && (
            <p className="dim small">{s.notLoaded} loaded alert{s.notLoaded === 1 ? "" : "s"} belong to hosts not in the loaded host list; their source is unknown and they are not counted in this scope.</p>
          )}
          {s.top.length === 0 && exact && <p className="dim">No active alerts in the selected sources.</p>}
          {s.top.length > 0 && (
            <div className="table-wrap">
              <table className="tbl">
                <thead><tr><th>Severity</th><th>Condition</th><th>Host</th><th>Opened</th><th>State</th><th><span className="sr-only">Actions</span></th></tr></thead>
                <tbody>
                  {s.top.map((a) => <AlertRow key={a.id} a={a} now={p.now} carry={p.carry} ack={p.ack} ackPending={p.ackPending} />)}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      <p className="small more"><Link to={`/alerts${p.carry}`}>All active alerts →</Link></p>
    </>
  );
}

function AlertRow({ a, now, carry, ack, ackPending }: { a: Alert; now: number; carry: string; ack: (id: string) => void; ackPending: boolean }) {
  return (
    <tr>
      <td><HealthBadge status={a.severity as "critical" | "warning"} /></td>
      <td className="small">{conditionText(a)}</td>
      <td><Link className="hostlink" to={`/hosts/${encodeURIComponent(a.host_id)}${carry}`}>{a.host_id}</Link></td>
      <td className="small dim" title={a.opened_at}>{ago(secondsSince(a.opened_at, now))}</td>
      <td className="small">
        {a.state === "open" ? (
          <button type="button" className="btn" disabled={ackPending} aria-label={`Acknowledge alert for ${a.host_id}`} onClick={() => ack(a.id)}>Acknowledge</button>
        ) : (
          <span className="dim">{a.acknowledged_by ? `Acknowledged by ${a.acknowledged_by}` : "Acknowledged"}</span>
        )}
      </td>
      <td className="small"><Link to={`/alerts/${encodeURIComponent(a.id)}${carry}`} aria-label={`Investigate alert for ${a.host_id}`}>Investigate</Link></td>
    </tr>
  );
}
