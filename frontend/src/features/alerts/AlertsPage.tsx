import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import { useAlert, useAlertMutations, useAlerts } from "../../api/hooks";
import type { Alert, AlertEvent, AlertStatusFilter, HealthStatus } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { HealthBadge } from "../../components/ui/Badges";
import { DASH, timestamp } from "../../lib/format";

const FILTERS: { value: AlertStatusFilter; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "resolved", label: "Resolved" },
  { value: "all", label: "All" },
];

export const METRIC_NAME: Record<string, string> = {
  "system.cpu.utilization": "CPU utilisation",
  "system.memory.utilization": "Memory utilisation",
  "system.disk.utilization": "Disk utilisation",
};

const EVENT_LABEL: Record<AlertEvent["event"], string> = {
  opened: "Opened",
  severity_changed: "Severity changed",
  acknowledged: "Acknowledged",
  recovered: "Recovered",
  resolved_manually: "Resolved by an operator",
};

const STATE_LABEL: Record<Alert["state"], string> = { open: "Open", acknowledged: "Acknowledged", resolved: "Resolved" };

/** What the alert says, in words. A threshold alert shows the value that crossed the line; nothing is guessed. */
export function conditionText(a: Pick<Alert, "rule" | "metric" | "labels" | "severity" | "value" | "threshold">): string {
  if (a.rule === "host_offline") return "Host has stopped reporting telemetry";
  const name = (a.metric && METRIC_NAME[a.metric]) || a.metric || "Metric";
  const mount = a.labels.mount ? ` (${a.labels.mount})` : "";
  const value = a.value == null ? DASH : `${a.value.toFixed(1)}%`;
  const limit = a.threshold == null ? DASH : `${a.threshold.toFixed(0)}%`;
  return `${name}${mount} ${value}, at or above the ${a.severity === "critical" ? "critical" : "warning"} limit of ${limit}`;
}

/** A failed operation, in words. Nothing about the request is echoed. */
function failureText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.isNetwork) return "Could not reach the UptimeTrace API. Nothing was changed.";
    if (e.code === "alert_resolved") return "This alert was already resolved, so it cannot be acknowledged.";
    if (e.code === "alert_not_found") return "That alert no longer exists.";
    return `${e.message || "The request failed."}`.trim();
  }
  return "The request failed. Nothing was changed.";
}

export function AlertsPage() {
  const [status, setStatus] = useState<AlertStatusFilter>("active");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const alerts = useAlerts(status);
  const m = useAlertMutations();

  return (
    <>
      <PageHeader
        title="Alerts"
        subtitle="Breaches and silent hosts, raised from stored telemetry. An alert opens once, updates when it escalates, and closes when its condition recovers."
      />

      <div className="toolbar" role="group" aria-label="Alert status">
        {FILTERS.map((f) => (
          <button key={f.value} type="button" className="btn" aria-pressed={status === f.value} onClick={() => setStatus(f.value)}>
            {f.label}
          </button>
        ))}
      </div>

      {m.acknowledge.isError && <div className="banner warn" role="alert">Could not acknowledge the alert. {failureText(m.acknowledge.error)}</div>}
      {m.resolve.isError && <div className="banner warn" role="alert">Could not resolve the alert. {failureText(m.resolve.error)}</div>}

      {alerts.isPending && <Loading label="Loading alerts" />}
      {alerts.isError && !alerts.data && <ErrorState error={alerts.error} onRetry={() => alerts.refetch()} />}
      {alerts.data && (alerts.data.alerts.length === 0 ? (
        <EmptyState
          title={status === "resolved" ? "No resolved alerts" : "No active alerts"}
          body="Alerts open when a host breaches a threshold or stops reporting, and close by themselves when it recovers. Nothing here means the monitored hosts are within their limits."
        />
      ) : (
        <div className="widget table-wrap">
          <table className="tbl">
            <thead>
              <tr><th>Severity</th><th>Host</th><th>Condition</th><th>State</th><th>Opened</th><th>Last evaluated</th><th><span className="sr-only">Actions</span></th></tr>
            </thead>
            <tbody>
              {alerts.data.alerts.map((a) => (
                <Fragment key={a.id}>
                  <tr className={expanded === a.id ? "row-selected" : undefined}>
                    <td><HealthBadge status={a.severity as HealthStatus} /></td>
                    <td><Link className="mono" to={`/hosts/${encodeURIComponent(a.host_id)}`}>{a.host_id}</Link></td>
                    <td>{conditionText(a)}</td>
                    <td>
                      {STATE_LABEL[a.state]}
                      {a.state === "resolved" && <div className="dim small">{a.resolution === "recovered" ? "Recovered" : "Resolved manually"}</div>}
                      {a.acknowledged_by && a.state !== "open" && <div className="dim small">Acknowledged by {a.acknowledged_by}</div>}
                    </td>
                    <td className="mono small" title={a.opened_at}>{timestamp(a.opened_at)}</td>
                    <td className="mono small" title={a.last_evaluated_at}>{timestamp(a.last_evaluated_at)}</td>
                    <td>
                      <div className="row-actions">
                        {a.state === "open" && (
                          <button type="button" className="btn" aria-label={`Acknowledge alert for ${a.host_id}`} disabled={m.acknowledge.isPending}
                            onClick={() => m.acknowledge.mutate(a.id)}>Acknowledge</button>
                        )}
                        {a.state !== "resolved" && (confirming === a.id ? (
                          <span className="confirm" role="group" aria-label={`Confirm resolving the alert for ${a.host_id}`}>
                            <span>Resolve by hand? If the breach is still there, a new alert opens at the next evaluation.</span>{" "}
                            <button type="button" className="btn danger" disabled={m.resolve.isPending}
                              onClick={() => m.resolve.mutate(a.id, { onSettled: () => setConfirming(null) })}>
                              {m.resolve.isPending ? "Resolving…" : "Confirm resolve"}
                            </button>{" "}
                            <button type="button" className="btn" onClick={() => setConfirming(null)}>Cancel</button>
                          </span>
                        ) : (
                          <button type="button" className="btn" aria-label={`Resolve alert for ${a.host_id}`} onClick={() => setConfirming(a.id)}>Resolve</button>
                        ))}
                        <Link className="btn" to={`/alerts/${encodeURIComponent(a.id)}`} aria-label={`Investigate alert for ${a.host_id}`}>Investigate</Link>
                        <button type="button" className="btn" aria-expanded={expanded === a.id} aria-label={`Timeline for ${a.host_id}`}
                          onClick={() => setExpanded(expanded === a.id ? null : a.id)}>
                          {expanded === a.id ? "Hide timeline" : "Timeline"}
                        </button>
                      </div>
                    </td>
                  </tr>
                  {expanded === a.id && (
                    <tr>
                      <td colSpan={7}><Timeline id={a.id} /></td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  );
}

function Timeline({ id }: { id: string }) {
  const d = useAlert(id);
  if (d.isPending) return <Loading label="Loading the alert timeline" />;
  if (d.isError) return <ErrorState error={d.error} onRetry={() => d.refetch()} />;
  if (!d.data) return null;
  return (
    <ol className="timeline" aria-label="Alert timeline">
      {d.data.events.map((e, i) => (
        <li key={`${e.occurred_at}-${i}`}>
          <span className="mono small">{timestamp(e.occurred_at)}</span> <b>{EVENT_LABEL[e.event]}</b>
          {e.event === "opened" && typeof e.detail.value === "number" && <span className="dim"> · value {Number(e.detail.value).toFixed(1)}</span>}
          {e.event === "severity_changed" && <span className="dim"> · {String(e.detail.from)} → {String(e.detail.to)}</span>}
          {e.event === "recovered" && typeof e.detail.value === "number" && <span className="dim"> · back to {Number(e.detail.value).toFixed(1)}</span>}
          <span className="dim small"> · {e.actor}</span>
        </li>
      ))}
    </ol>
  );
}
