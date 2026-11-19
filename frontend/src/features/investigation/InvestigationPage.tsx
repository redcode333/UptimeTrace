import { Link, useParams } from "react-router-dom";
import { useState } from "react";
import { ApiError } from "../../api/client";
import { useCreateInvestigationReport, useInvestigation, useInvestigationReport } from "../../api/hooks";
import type { HealthStatus, Investigation, InvestigationRecord, InvestigationRelated, InvestigationSeries } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { HealthBadge } from "../../components/ui/Badges";
import { TimeSeriesChart } from "../../components/ui/TimeSeriesChart";
import { conditionText, METRIC_NAME } from "../alerts/AlertsPage";
import { DASH, timestamp } from "../../lib/format";

const WINDOWS = [15, 30, 60, 120];

/** A related alert named by what it watches (no value: the related list does not claim one). */
function relatedName(r: InvestigationRelated): string {
  if (r.rule === "host_offline") return "Host has stopped reporting telemetry";
  const mount = r.labels.mount ? ` (${r.labels.mount})` : "";
  return `${(r.metric && METRIC_NAME[r.metric]) || r.metric || "Metric"}${mount}`;
}

const EVENT_LABEL: Record<string, string> = {
  opened: "Opened",
  severity_changed: "Severity changed",
  acknowledged: "Acknowledged",
  recovered: "Recovered",
  resolved_manually: "Resolved by an operator",
};

const UNAVAILABLE = "Not recorded";

function value(v: number | null | undefined, unit = "%"): string {
  return v == null ? UNAVAILABLE : `${v.toFixed(1)}${unit}`;
}

function when(t: string | null | undefined): string {
  return t == null ? UNAVAILABLE : timestamp(t);
}

export function InvestigationPage() {
  const { alertId } = useParams<{ alertId: string }>();
  const [windowMinutes, setWindowMinutes] = useState(30);
  const inv = useInvestigation(alertId, windowMinutes);

  return (
    <>
      <PageHeader
        title="Alert investigation"
        subtitle="Why this alert fired, the telemetry around it, what followed, and whether the host recovered. Everything shown is stored evidence; nothing is inferred."
      />
      <p className="small"><Link to="/alerts">← Back to alerts</Link></p>

      {inv.isPending && <Loading label="Loading the investigation" />}
      {inv.isError && !inv.data && (inv.error instanceof ApiError && inv.error.code === "alert_not_found"
        ? <EmptyState title="No such alert" body="This alert does not exist in this workspace, or it was removed." />
        : <ErrorState error={inv.error} onRetry={() => inv.refetch()} />)}

      {inv.data && (
        <>
          <div className="toolbar" role="group" aria-label="Telemetry window around the trigger">
            {WINDOWS.map((w) => (
              <button key={w} type="button" className="btn" aria-pressed={windowMinutes === w} onClick={() => setWindowMinutes(w)}>
                ±{w} min
              </button>
            ))}
          </div>
          <Summary inv={inv.data} />
          <div className="grid2">
            <TriggerEvidence inv={inv.data} />
            <RecoveryEvidence inv={inv.data} />
          </div>
          <TelemetrySection inv={inv.data} />
          <div className="grid2">
            <TimelineSection inv={inv.data} />
            <RelatedSection inv={inv.data} />
          </div>
          <RecordSection alertId={alertId!} />
        </>
      )}
    </>
  );
}

function Summary({ inv }: { inv: Investigation }) {
  const trigger = inv.trigger;
  return (
    <section className="widget" aria-labelledby="inv-summary">
      <h2 id="inv-summary">Summary</h2>
      <div className="row-actions">
        <HealthBadge status={inv.alert.severity as HealthStatus} />
        <span>{inv.alert.state === "resolved" ? "Resolved" : inv.alert.state === "acknowledged" ? "Acknowledged" : "Open"}</span>
        <span className="mono">{inv.alert.host_id}</span>
      </div>
      <p>
        {trigger.rule === "threshold" && trigger.metric
          ? conditionText({ rule: trigger.rule, metric: trigger.metric, labels: trigger.labels, severity: trigger.severity,
              value: trigger.trigger_value, threshold: trigger.threshold })
          : conditionText({ rule: "host_offline", metric: null, labels: {}, severity: trigger.severity, value: null, threshold: null })}
      </p>
      <p className="dim small">
        First fired {timestamp(trigger.first_fired_at)}. Evidence as of {timestamp(inv.as_of)}. Telemetry window ±{inv.window_minutes} minutes.
      </p>
    </section>
  );
}

function TriggerEvidence({ inv }: { inv: Investigation }) {
  const t = inv.trigger;
  const isThreshold = t.rule === "threshold";
  return (
    <section className="widget" aria-labelledby="inv-trigger">
      <h2 id="inv-trigger">What triggered it</h2>
      <dl className="kv">
        <dt>Rule</dt><dd>{t.rule === "threshold" ? "Threshold" : "Host offline"}</dd>
        <dt>Metric</dt><dd className="mono">{t.metric ?? DASH}{Object.keys(t.labels).length ? ` ${JSON.stringify(t.labels)}` : ""}</dd>
        {isThreshold && <>
          <dt>Observed value</dt><dd>{value(t.trigger_value)}</dd>
          <dt>Limit</dt><dd>{t.comparison ?? UNAVAILABLE} {value(t.threshold)} ({t.severity})</dd>
        </>}
        <dt>Sample observed</dt><dd>{when(t.trigger_observed_at)}</dd>
        <dt>First fired</dt><dd>{timestamp(t.first_fired_at)}</dd>
        <dt>Last evaluated</dt><dd>{timestamp(t.last_evaluated_at)}</dd>
      </dl>
      {t.unavailable.length > 0 && (
        <p className="dim small">Not recorded for this alert: {t.unavailable.join(", ").replace(/_/g, " ")}. It was never inferred.</p>
      )}
      <p className="small">
        Limits in force: warning {value(inv.settings.warning_limit)}, critical {value(inv.settings.critical_limit)}
        {" "}(settings version {inv.settings.version}).
      </p>
      <p className="small">Host now: {inv.host.known && inv.host.current
        ? <><HealthBadge status={inv.host.current.health.status} /> {inv.host.current.reporting_status}, last telemetry {timestamp(inv.host.current.last_seen)}</>
        : "no telemetry on record"}</p>
    </section>
  );
}

function RecoveryEvidence({ inv }: { inv: Investigation }) {
  const r = inv.recovery;
  return (
    <section className="widget" aria-labelledby="inv-recovery">
      <h2 id="inv-recovery">Recovery</h2>
      {r.recovered ? (
        <dl className="kv">
          <dt>Recovered</dt><dd>Yes, by telemetry</dd>
          {inv.trigger.rule === "threshold" && <><dt>Recovery value</dt><dd>{value(r.value)}</dd></>}
          <dt>Evidence observed</dt><dd>{when(r.observed_at)}</dd>
          <dt>Closed</dt><dd>{when(r.resolved_at)}</dd>
        </dl>
      ) : inv.alert.resolution === "manual" ? (
        <p>Closed manually by <b>{r.resolved_by}</b> at {when(r.resolved_at)}. Telemetry does not show that the condition cleared.</p>
      ) : (
        <p>Not recovered. The alert is {inv.alert.state}; no telemetry has cleared it.</p>
      )}
    </section>
  );
}

function TelemetrySection({ inv }: { inv: Investigation }) {
  return (
    <section className="widget" aria-labelledby="inv-telemetry">
      <h2 id="inv-telemetry">Telemetry around the alert</h2>
      {inv.series_note && <p className="dim">{inv.series_note}</p>}
      {inv.series.length === 0 && !inv.series_note && <p className="dim">No telemetry series for this metric in the window.</p>}
      {inv.series.map((s) => <SeriesPanel key={s.window} s={s} inv={inv} />)}
    </section>
  );
}

function SeriesPanel({ s, inv }: { s: InvestigationSeries; inv: Investigation }) {
  const label = s.window === "around_trigger" ? `Around the trigger, ±${inv.window_minutes} min` : `Around the recovery, ±${inv.window_minutes} min`;
  const hasData = s.points.some((p) => p.value != null);
  const series = [{ labels: s.labels, unit: "percent", value_type: "gauge" as const, points: s.points.map((p) => ({ t: p.t, value: p.value, samples: p.samples })) }];
  return (
    <div>
      <h3 className="small">{label}</h3>
      {hasData ? (
        <TimeSeriesChart series={series} unit="percent" start={s.start} end={s.end} label={`${s.metric ?? "metric"} ${label}`} height={180} />
      ) : (
        <p className="dim small">No samples in this window. Gaps are shown as missing, never as zero.</p>
      )}
    </div>
  );
}

function TimelineSection({ inv }: { inv: Investigation }) {
  return (
    <section className="widget" aria-labelledby="inv-timeline">
      <h2 id="inv-timeline">Lifecycle</h2>
      <ol className="timeline" aria-label="Alert lifecycle">
        {inv.events.map((e, i) => (
          <li key={`${e.occurred_at}-${i}`}>
            <span className="mono small">{timestamp(e.occurred_at)}</span> <b>{EVENT_LABEL[e.event] ?? e.event}</b>
            {e.event === "opened" && typeof e.detail.value === "number" && <span className="dim"> · value {Number(e.detail.value).toFixed(1)}</span>}
            {e.event === "recovered" && typeof e.detail.value === "number" && <span className="dim"> · back to {Number(e.detail.value).toFixed(1)}</span>}
            <span className="dim small"> · {e.actor}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function RelatedSection({ inv }: { inv: Investigation }) {
  const rel = inv.related;
  return (
    <section className="widget" aria-labelledby="inv-related">
      <h2 id="inv-related">Related alerts on this host</h2>
      <p className="dim small">{rel.note} Window ±{rel.window_minutes} min.</p>
      {rel.items.length === 0 ? (
        <p className="dim">No other alerts on this host opened in the window.</p>
      ) : (
        <ul className="plain">
          {rel.items.map((r) => (
            <li key={r.id}>
              <Link to={`/alerts/${encodeURIComponent(r.id)}`}>
                {relatedName(r)}
              </Link>
              <span className="dim small"> · {r.severity} · {r.state} · opened {timestamp(r.opened_at)}</span>
            </li>
          ))}
        </ul>
      )}
      {rel.total > rel.items.length && <p className="dim small">Showing {rel.items.length} of {rel.total}.</p>}
    </section>
  );
}

function RecordSection({ alertId }: { alertId: string }) {
  const rec = useInvestigationReport(alertId);
  const create = useCreateInvestigationReport(alertId);
  const none = rec.error instanceof ApiError && rec.error.code === "report_not_found";
  const record: InvestigationRecord | undefined = create.data ?? rec.data;
  return (
    <section className="widget" aria-labelledby="inv-record">
      <h2 id="inv-record">Investigation record</h2>
      {rec.isPending && <Loading label="Loading the record" />}
      {rec.isError && !none && !create.data && <ErrorState error={rec.error} onRetry={() => rec.refetch()} />}
      {!record && !rec.isPending && (
        <>
          <p className="dim">No record exists for this alert yet. Creating one stores the evidence as it is now, so it can be reread later.</p>
          <button type="button" className="btn" disabled={create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? "Creating…" : "Create investigation record"}
          </button>
          {create.isError && <p className="banner warn" role="alert">The record could not be created. Nothing was changed.</p>}
        </>
      )}
      {record && <RecordBody rec={record} />}
    </section>
  );
}

function RecordBody({ rec }: { rec: InvestigationRecord }) {
  return (
    <>
      <p className="small">
        Recorded {timestamp(rec.created_at)} by {rec.created_by}. Digest <span className="mono">{rec.evidence_digest.slice(0, 16)}…</span>{" "}
        {rec.verified ? <span>— verified: the stored evidence reproduces these conclusions.</span>
          : <span className="banner warn" role="alert">Verification failed: the stored record does not match its evidence.</span>}
      </p>
      <h3 className="small">Observed facts</h3>
      <ul className="plain">{rec.body.facts.map((f) => <li key={f.id}><span className="mono small">{f.id}</span> {f.text}</li>)}</ul>
      <h3 className="small">Conclusions (fixed rules applied to the facts)</h3>
      <ul className="plain">{rec.body.conclusions.map((c) => <li key={c.id}><span className="mono small">{c.id}</span> {c.text} <span className="dim small">from {c.basis.join(", ")}</span></li>)}</ul>
      <h3 className="small">Not established</h3>
      <ul className="plain">{rec.body.not_established.map((n, i) => <li key={i}>{n}</li>)}</ul>
    </>
  );
}
