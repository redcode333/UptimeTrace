import { Link, useParams } from "react-router-dom";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { useAlertMutations, useCreateInvestigationReport, useInvestigation, useInvestigationReport, useTrace } from "../../api/hooks";
import type { HealthStatus, Investigation, InvestigationRecord, InvestigationRelated, InvestigationSeries, InvestigationTraces, TraceReason } from "../../api/types";
import { useRehearsalsForAlert } from "../../api/rehearsalHooks";
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
  const raised = useRehearsalsForAlert(alertId);
  const rehearsal = raised.data?.rehearsals[0] ?? null;

  return (
    <>
      <PageHeader
        title="Alert investigation"
        subtitle="Why this alert fired, the telemetry around it, what followed, and whether the host recovered. Everything shown is stored evidence; nothing is inferred."
      />
      <p className="small"><Link to="/alerts">← Back to alerts</Link></p>
      {rehearsal && (
        <div className="banner notice" role="note">
          <span>
            <b>RAISED BY A REHEARSAL</b> This alert came from a Failure Rehearsal on synthetic host <span className="mono">{rehearsal.host_id}</span>. It is telemetry-only evidence, not a real machine fault.
          </span>
          <Link className="btn" to={`/rehearsal/${encodeURIComponent(rehearsal.id)}`}>Open rehearsal</Link>
        </div>
      )}

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
          <LifecycleActions inv={inv.data} />
          <div className="grid2">
            <TriggerEvidence inv={inv.data} />
            <RecoveryEvidence inv={inv.data} />
          </div>
          <TelemetrySection inv={inv.data} />
          <div className="grid2">
            <TimelineSection inv={inv.data} />
            <RelatedSection inv={inv.data} />
          </div>
          <TracesSection traces={inv.data.traces} />
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
        <Link className="mono" to={`/hosts/${encodeURIComponent(inv.alert.host_id)}`}>{inv.alert.host_id}</Link>
        <Link to="/investigations">All investigations</Link>
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

const FAILURE: Record<string, string> = {
  alert_resolved: "This alert is already resolved, so it cannot be acknowledged.",
  alert_not_found: "This alert no longer exists in this workspace.",
};

function failureOf(e: unknown): string {
  if (e instanceof ApiError && FAILURE[e.code]) return FAILURE[e.code]!;
  return "The request did not complete. Nothing was changed.";
}

/** Lifecycle actions on the alert this investigation belongs to. Same rules as the Alerts page: acknowledge while open, resolve by hand (confirmed) until resolved. */
function LifecycleActions({ inv }: { inv: Investigation }) {
  const m = useAlertMutations();
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [outcome, setOutcome] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const id = inv.alert.id;
  const state = inv.alert.state;
  const refreshInvestigation = () => qc.invalidateQueries({ queryKey: ["investigation"] });
  return (
    <section className="widget" aria-labelledby="inv-actions">
      <h2 id="inv-actions">Alert actions</h2>
      <p className="small">
        Current state: <b>{state === "open" ? "Open" : state === "acknowledged" ? "Acknowledged" : "Resolved"}</b>
        {state === "resolved" && inv.alert.resolution && <> ({inv.alert.resolution === "recovered" ? "recovered by telemetry" : "resolved by hand"})</>}.
        {" "}These actions change the alert itself; the evidence on this page is not altered.
      </p>
      <div className="row-actions">
        {state === "open" && (
          <button type="button" className="btn" disabled={m.acknowledge.isPending}
            onClick={() => m.acknowledge.mutate(id, {
              onSuccess: (a) => { setOutcome({ tone: "ok", text: `Acknowledged by ${a.acknowledged_by ?? "you"} at ${a.acknowledged_at ? timestamp(a.acknowledged_at) : "now"}.` }); void refreshInvestigation(); },
              onError: (e) => setOutcome({ tone: "warn", text: failureOf(e) }),
            })}>
            {m.acknowledge.isPending ? "Acknowledging…" : "Acknowledge"}
          </button>
        )}
        {state === "acknowledged" && <span className="dim small">Already acknowledged.</span>}
        {state === "resolved" && <span className="dim small">Resolved alerts cannot be acknowledged.</span>}
        {state !== "resolved" && !confirming && (
          <button type="button" className="btn" onClick={() => { setConfirming(true); setOutcome(null); }}>Resolve by hand</button>
        )}
      </div>
      {state !== "resolved" && confirming && (
        <div className="confirm" role="group" aria-label="Confirm resolving this alert">
          <span>Resolve by hand? If the breach is still there, a new alert opens at the next evaluation.</span>{" "}
          <button type="button" className="btn danger" disabled={m.resolve.isPending}
            onClick={() => m.resolve.mutate(id, {
              onSuccess: (a) => {
                setConfirming(false);
                setOutcome({ tone: "ok", text: `Resolved by hand by ${a.resolved_by ?? "you"} at ${a.resolved_at ? timestamp(a.resolved_at) : "now"}.` });
                void refreshInvestigation();
              },
              onError: (e) => { setConfirming(false); setOutcome({ tone: "warn", text: failureOf(e) }); },
            })}>
            {m.resolve.isPending ? "Resolving…" : "Confirm resolve"}
          </button>{" "}
          <button type="button" className="btn" onClick={() => setConfirming(false)}>Cancel</button>
        </div>
      )}
      {outcome && <p className={`banner ${outcome.tone === "ok" ? "notice" : "warn"}`} role={outcome.tone === "warn" ? "alert" : "status"}>{outcome.text}</p>}
      <p className="dim small">Lifecycle actions are open to every signed-in member of the workspace. The product has no role model yet, so there is no finer permission to check.</p>
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

const TRACE_REASON: Record<Exclude<TraceReason, null>, string> = {
  not_configured: "Application tracing is not connected to this deployment. The evidence above is unaffected.",
  no_registered_services: "No registered service runs on this host, so no traces can be attributed to it. Assign a service in Infrastructure.",
  unavailable: "The trace backend could not be reached, so no trace evidence is shown for this window. The evidence above is unaffected.",
};

/** Application traces from this host's registered services in the window. A slow or failing span is evidence, not a cause. */
function TracesSection({ traces }: { traces: InvestigationTraces }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section className="widget" aria-labelledby="inv-traces">
      <h2 id="inv-traces">Application traces</h2>
      {!traces.available ? (
        <p className="dim">{TRACE_REASON[traces.reason ?? "unavailable"]}</p>
      ) : traces.items.length === 0 ? (
        <p className="dim">No traces from this host's services in the window ({traces.services.join(", ")}).</p>
      ) : (
        <>
          <p className="dim small">Traces from {traces.services.join(", ")} in the window. Slow spans are at least 1 s; evidence of where time went, not of a cause.</p>
          <ul className="plain" aria-label="Traces in the window">
            {traces.items.map((t) => (
              <li key={t.trace_id}>
                <div>
                  <span className="mono">{t.trace_id.slice(0, 8)}</span> {t.root.service} · {t.root.operation}
                  <span className="dim small"> · {timestamp(t.started_at)} · {Math.round(t.duration_ms)} ms · {t.span_count} spans</span>
                  {t.error_count > 0 && <span className="small"> · {t.error_count} error span{t.error_count === 1 ? "" : "s"}</span>}
                  {t.slow_span_count > 0 && <span className="small"> · {t.slow_span_count} slow</span>}
                  <button type="button" className="btn" aria-expanded={open === t.trace_id}
                    aria-label={`Inspect trace ${t.trace_id.slice(0, 8)}`} onClick={() => setOpen(open === t.trace_id ? null : t.trace_id)}>
                    {open === t.trace_id ? "Hide" : "Inspect"}
                  </button>
                </div>
                <p className="dim small">Slowest: {t.slowest.service} · {t.slowest.operation} ({Math.round(t.slowest.duration_ms)} ms)</p>
                {open === t.trace_id && <TraceSpans traceId={t.trace_id} />}
              </li>
            ))}
          </ul>
          {traces.truncated && <p className="dim small">Showing the first {traces.items.length} traces.</p>}
        </>
      )}
    </section>
  );
}

function TraceSpans({ traceId }: { traceId: string }) {
  const q = useTrace(traceId);
  if (q.isPending) return <Loading label="Loading the trace" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  // Bars are placed on the trace's own timeline: start and length as a share of the whole trace, so the service sequence
  // and where the time went read straight off the table.
  const total = Math.max(1, q.data.summary.duration_ms);
  return (
    <div className="table-wrap">
      <table aria-label={`Spans of trace ${traceId.slice(0, 8)}`}>
        <thead><tr><th scope="col">Service</th><th scope="col">Operation</th><th scope="col">Timeline</th><th scope="col">Start</th><th scope="col">Duration</th><th scope="col">Flags</th></tr></thead>
        <tbody>
          {q.data.spans.map((s) => (
            <tr key={s.span_id}>
              <td>{s.service}</td>
              <td style={{ paddingLeft: s.parent_span_id ? 16 : 0 }}>{s.operation}</td>
              <td style={{ minWidth: 160 }}>
                <div className="trace-track" aria-hidden="true">
                  <span className={`trace-bar${s.error ? " is-error" : s.slow ? " is-slow" : ""}`}
                    style={{ left: `${Math.min(100, (s.offset_ms / total) * 100)}%`, width: `${Math.max(0.6, Math.min(100, (s.duration_ms / total) * 100))}%` }} />
                </div>
              </td>
              <td>+{Math.round(s.offset_ms)} ms</td>
              <td>{Math.round(s.duration_ms)} ms</td>
              <td>{[s.error && "error", s.slow && "slow"].filter(Boolean).join(", ") || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {q.data.truncated && <p className="dim small">Showing the first spans of a very large trace.</p>}
    </div>
  );
}

function RelatedSection({ inv }: { inv: Investigation }) {
  const rel = inv.related;
  return (
    <section className="widget" aria-labelledby="inv-related">
      <h2 id="inv-related">Correlated alerts on this host</h2>
      <p className="dim small">{rel.note} Window ±{rel.window_minutes} min. Correlation is not a proven cause.</p>
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
  const [checked, setChecked] = useState<string | null>(null);
  const none = rec.error instanceof ApiError && rec.error.code === "report_not_found";
  const record: InvestigationRecord | undefined = create.data ?? rec.data;
  const recheck = async () => {
    setChecked(null);
    const r = await rec.refetch();
    if (r.data) {
      setChecked(r.data.verified
        ? `Re-checked ${timestamp(new Date().toISOString())}: the digest and the re-derived conclusions both match the stored evidence.`
        : `Re-checked ${timestamp(new Date().toISOString())}: digest ${r.data.digest_matches ? "matches" : "does not match"}, re-derivation ${r.data.rederivation_matches ? "matches" : "does not match"}.`);
    } else if (r.error) setChecked("The record could not be re-checked. Nothing was changed.");
  };
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
      {record && (
        <div className="row-actions">
          <button type="button" className="btn" disabled={rec.isFetching} onClick={recheck}>{rec.isFetching ? "Re-checking…" : "Re-check record"}</button>
          {checked && <span className="small" role="status">{checked}</span>}
        </div>
      )}
    </section>
  );
}

function RecordBody({ rec }: { rec: InvestigationRecord }) {
  return (
    <>
      <p className="small">
        Recorded {timestamp(rec.created_at)} by {rec.created_by}. SHA-256 digest <span className="mono" style={{ overflowWrap: "anywhere" }}>{rec.evidence_digest}</span>{" "}
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
