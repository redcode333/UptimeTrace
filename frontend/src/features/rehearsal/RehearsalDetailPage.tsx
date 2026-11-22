import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useRehearsal, useRehearsalMutations } from "../../api/rehearsalHooks";
import type { Rehearsal } from "../../api/rehearsalTypes";
import { PageHeader } from "../../components/PageHeader";
import { ErrorState, Loading, NotAvailable } from "../../components/states/States";
import { DASH, timestamp } from "../../lib/format";
import {
  DETECTION_LABEL, detectionTone, EXECUTION_LABEL, evidenceCopy, executionTone, formatSeconds, isRunning, PHASE_LABEL, progressPercent,
} from "../../lib/rehearsal";
import "../../styles/rehearsal.css";
import { rehearsalFailure } from "./rehearsalErrors";

/** One rehearsal: what ran, what the execution did, what UptimeTrace detected, and the telemetry that was sent. */
export function RehearsalDetailPage() {
  const { rehearsalId } = useParams();
  const q = useRehearsal(rehearsalId);
  const m = useRehearsalMutations();
  const [confirmingStop, setConfirmingStop] = useState(false);

  if (q.isPending) return <Loading label="Loading rehearsal" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const r = q.data;

  const stopRequested = r.stop_requested_at !== null;

  return (
    <>
      <PageHeader
        title={`Rehearsal on ${r.host_id}`}
        subtitle={`${r.scenario} · started ${timestamp(r.started_at ?? r.created_at)}`}
        actions={isRunning(r) && !stopRequested ? (
          confirmingStop ? (
            <span className="confirm" role="group" aria-label="Confirm stopping the rehearsal">
              <span className="dim small">Stop now? The fault is ended with recovery telemetry.</span>
              <button type="button" className="btn danger" disabled={m.stop.isPending}
                onClick={() => m.stop.mutate(r.id, { onSettled: () => setConfirmingStop(false) })}>
                {m.stop.isPending ? "Stopping…" : "Confirm stop"}
              </button>
              <button type="button" className="btn" onClick={() => setConfirmingStop(false)}>Keep running</button>
            </span>
          ) : (
            <button type="button" className="btn danger" onClick={() => setConfirmingStop(true)}>Stop rehearsal</button>
          )
        ) : undefined}
      />

      <div className="banner notice rehearsal-scope" role="note">
        <span><b>TELEMETRY ONLY</b> Synthetic host. Nothing was run on any machine.</span>
      </div>

      {m.stop.isError && <div className="banner warn" role="alert">{rehearsalFailure(m.stop.error)}</div>}

      {isRunning(r) && <Progress r={r} stopRequested={stopRequested} />}

      <div className="rehearsal-grid">
        <Verdict title="Execution" kind="execution" r={r} />
        <Verdict title="Detection" kind="detection" r={r} />
      </div>

      <div className="rehearsal-grid">
        <section className="widget rehearsal-panel" aria-labelledby="reh-facts">
          <div className="widget-head"><h2 id="reh-facts">Run</h2></div>
          <div className="widget-body">
            <dl className="kv">
              <dt>Target</dt><dd><Link className="mono" to={`/hosts/${encodeURIComponent(r.host_id)}`}>{r.host_id}</Link></dd>
              <dt>Scenario</dt><dd>{r.scenario}</dd>
              <dt>Started</dt><dd className="mono small">{timestamp(r.started_at ?? r.created_at)}</dd>
              <dt>Ended</dt><dd className="mono small">{r.ended_at ? timestamp(r.ended_at) : DASH}</dd>
              <dt>Started by</dt><dd>{r.created_by}</dd>
              <dt>Stop</dt><dd>{stopRequested ? `Requested by ${r.stop_requested_by ?? "an operator"} at ${timestamp(r.stop_requested_at!)}` : "Not requested"}</dd>
              <dt>Envelopes</dt>
              <dd>{r.accepted_envelopes} accepted, {r.failed_envelopes} not accepted, of {r.planned_envelopes} planned</dd>
              <dt>Fault onset</dt>
              <dd className="mono small">
                {r.fault_confirmed_at
                  ? `${timestamp(r.fault_confirmed_at)}, ${metricName(r)} ${r.fault_observed_value?.toFixed(1) ?? DASH}% in stored telemetry`
                  : "Not confirmed in stored telemetry"}
              </dd>
              <dt>Cleanup</dt><dd>{cleanupCopy(r)}</dd>
            </dl>
          </div>
        </section>

        <section className="widget rehearsal-panel" aria-labelledby="reh-evidence">
          <div className="widget-head"><h2 id="reh-evidence">Alert and evidence</h2></div>
          <div className="widget-body">
            <dl className="kv">
              <dt>Alert</dt>
              <dd>
                {r.alert_id ? (
                  <Link className="mono" to={`/alerts/${encodeURIComponent(r.alert_id)}`}>Open alert</Link>
                ) : "No alert opened for this rehearsal"}
              </dd>
              <dt>Opened</dt><dd className="mono small">{r.alert_opened_at ? timestamp(r.alert_opened_at) : DASH}</dd>
              <dt>Resolved</dt><dd className="mono small">{r.alert_resolved_at ? timestamp(r.alert_resolved_at) : DASH}</dd>
              <dt>Detection delay</dt>
              <dd className="mono">{r.detection_delay_seconds === null ? DASH : formatSeconds(r.detection_delay_seconds)}</dd>
              <dt>Recovery</dt><dd>{recoveryCopy(r)}</dd>
              <dt>Investigation</dt><dd>{evidenceCopy(r)}</dd>
            </dl>
          </div>
        </section>
      </div>

      <section aria-labelledby="reh-steps" className="rehearsal-recent">
        <h2 id="reh-steps" className="section-title">Telemetry sent</h2>
        {!r.steps || r.steps.length === 0 ? (
          <NotAvailable title="No envelopes recorded yet" reason="Each envelope the rehearsal sends appears here with the value read back from storage." />
        ) : (
          <div className="widget table-wrap">
            <table className="tbl">
              <thead>
                <tr><th>#</th><th>Sent</th><th>Phase</th><th>Outcome</th><th>{metricName(r)} read back</th><th>Error</th></tr>
              </thead>
              <tbody>
                {r.steps.map((s) => (
                  <tr key={s.seq}>
                    <td className="mono small">{s.seq}</td>
                    <td className="mono small">{timestamp(s.sent_at)}</td>
                    <td>{PHASE_LABEL[s.phase] ?? s.phase}</td>
                    <td>{s.outcome}</td>
                    <td className="mono small">{s.observed_value === null ? DASH : `${s.observed_value.toFixed(1)}%`}</td>
                    <td className="mono small">{s.error_class ?? DASH}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function Progress({ r, stopRequested }: { r: Rehearsal; stopRequested: boolean }) {
  const pct = progressPercent(r);
  return (
    <div className="banner notice rehearsal-progress" role="status" aria-live="polite">
      <span>
        <b>RUNNING</b> {r.phase ? `${PHASE_LABEL[r.phase] ?? r.phase} phase` : "Starting"} · {r.sent_envelopes} of {r.planned_envelopes} envelopes sent
        {r.failed_envelopes > 0 ? `, ${r.failed_envelopes} not accepted` : ""}
        {stopRequested ? " · stop requested, ending the fault" : ""}
      </span>
      <div className="rehearsal-bar" aria-hidden="true"><i style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

function Verdict({ title, kind, r }: { title: string; kind: "execution" | "detection"; r: Rehearsal }) {
  const label = kind === "execution" ? EXECUTION_LABEL[r.execution] : r.detection ? DETECTION_LABEL[r.detection] : "● PENDING";
  const tone = kind === "execution" ? executionTone(r.execution) : detectionTone(r.detection);
  const reason = kind === "execution" ? r.execution_reason : r.detection_reason;
  const heading = kind === "execution" ? "Did the injection run?" : "Did UptimeTrace detect it?";
  return (
    <section className={`widget rehearsal-panel verdict-card verdict-card-${tone}`} aria-labelledby={`reh-${kind}`}>
      <div className="widget-head"><h2 id={`reh-${kind}`}>{title}</h2></div>
      <div className="widget-body">
        <p className={`verdict verdict-${tone} verdict-large`}>{label}</p>
        <p className="dim small">{heading}</p>
        <p>{reason ?? (r.execution === "RUNNING" ? "The rehearsal is running." : "No reason recorded.")}</p>
      </div>
    </section>
  );
}

function cleanupCopy(r: Rehearsal): string {
  switch (r.cleanup_status) {
    case "not_needed": return "Not needed: the fault was ended by the scenario's own recovery telemetry, or it never started.";
    case "recovered": return `Recovery telemetry accepted${r.recovery_sent_at ? ` at ${timestamp(r.recovery_sent_at)}` : ""}.`;
    case "failed": return "The recovery telemetry was not accepted. The host may still report the fault.";
  }
}

function recoveryCopy(r: Rehearsal): string {
  if (r.recovery_observed === null) return "Not applicable: no alert was opened.";
  if (!r.recovery_observed) return "Not observed: the alert did not resolve by telemetry after recovery.";
  return `Observed: the alert resolved ${formatSeconds(r.recovery_seconds)} after recovery telemetry was sent.`;
}

/** The measured metric's name for display. Older rows without a metric are CPU rehearsals. */
function metricName(r: Rehearsal): string {
  if (r.metric === "system.memory.utilization") return "Memory";
  return "CPU";
}
