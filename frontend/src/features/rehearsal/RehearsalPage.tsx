import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { RehearsalScenario, RehearsalTarget } from "../../api/rehearsalTypes";
import { useRehearsalCatalogue, useRehearsalMutations, useRehearsals, useRehearsalTargets } from "../../api/rehearsalHooks";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { DASH, timestamp } from "../../lib/format";
import {
  confirmationMatches, DETECTION_LABEL, detectionTone, EXECUTION_LABEL, executionTone, formatSeconds, isRunning, targetLabel,
} from "../../lib/rehearsal";
import "../../styles/rehearsal.css";
import { rehearsalFailure } from "./rehearsalErrors";

/** Failure Rehearsal: choose an authorised synthetic host and a catalogue scenario, check what it affects, confirm, and run. */
export function RehearsalPage() {
  const navigate = useNavigate();
  const targets = useRehearsalTargets();
  const catalogue = useRehearsalCatalogue();
  const list = useRehearsals();
  const m = useRehearsalMutations();
  const [hostId, setHostId] = useState<string | null>(null);
  const [scenarioId, setScenarioId] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [typed, setTyped] = useState("");

  const running = list.data?.rehearsals.find(isRunning) ?? null;
  const scenario: RehearsalScenario | null = catalogue.data?.scenarios.find((s) => s.id === scenarioId) ?? null;
  const target: RehearsalTarget | null = targets.data?.find((t) => t.host_id === hostId && t.eligible) ?? null;
  const canRun = !!target && !!scenario && acknowledged && confirmationMatches(target.host_id, typed) && !running && !m.start.isPending;

  function run() {
    if (!target || !scenario || !canRun) return;
    m.start.mutate(
      { host_id: target.host_id, scenario: scenario.id, confirm_host_id: typed },
      { onSuccess: (r) => navigate(`/rehearsal/${encodeURIComponent(r.id)}`) },
    );
  }

  return (
    <>
      <PageHeader title="Failure Rehearsal" subtitle="Run a controlled failure against a registered synthetic host and check what UptimeTrace detects." />
      <div className="banner notice rehearsal-scope" role="note">
        <span>
          <b>TELEMETRY ONLY</b> A rehearsal sends telemetry for one synthetic host and checks UptimeTrace's own alerting and recovery.
          It does not load, stop or change any machine. Live and replayed hosts can never be targeted.
        </span>
      </div>

      {running && (
        <div className="banner warn" role="status">
          <span><b>A REHEARSAL IS RUNNING</b> on {running.host_id}.</span>
          <Link className="btn" to={`/rehearsal/${encodeURIComponent(running.id)}`}>Open it</Link>
        </div>
      )}

      <div className="rehearsal-grid">
        <section className="widget rehearsal-panel" aria-labelledby="reh-host">
          <div className="widget-head"><h2 id="reh-host">1 · Host</h2></div>
          <div className="widget-body">
            {targets.isPending && <Loading label="Loading hosts" />}
            {targets.isError && <ErrorState error={targets.error} onRetry={() => void targets.refetch()} />}
            {targets.data && targets.data.length === 0 && (
              <EmptyState title="No registered hosts" body="Register a synthetic host (its id starts with sim-) in the host registry first." />
            )}
            {targets.data && targets.data.length > 0 && (
              <fieldset className="rehearsal-choices">
                <legend className="sr-only">Host</legend>
                {targets.data.map((t) => (
                  <label key={t.host_id} className={`rehearsal-choice${t.eligible ? "" : " is-disabled"}`}>
                    <input
                      type="radio"
                      name="reh-host"
                      value={t.host_id}
                      disabled={!t.eligible}
                      checked={hostId === t.host_id}
                      onChange={() => { setHostId(t.host_id); setTyped(""); }}
                    />
                    <span>
                      <span className="mono">{targetLabel(t)}</span>
                      <span className="dim small">{t.kind} · {t.telemetry_state.replace(/_/g, " ")}</span>
                      {!t.eligible && t.reason && <span className="small rehearsal-refusal">{t.reason}</span>}
                    </span>
                  </label>
                ))}
              </fieldset>
            )}
          </div>
        </section>

        <section className="widget rehearsal-panel" aria-labelledby="reh-scenario">
          <div className="widget-head"><h2 id="reh-scenario">2 · Scenario</h2></div>
          <div className="widget-body">
            {catalogue.isPending && <Loading label="Loading scenarios" />}
            {catalogue.isError && <ErrorState error={catalogue.error} onRetry={() => void catalogue.refetch()} />}
            {catalogue.data && (
              <fieldset className="rehearsal-choices">
                <legend className="sr-only">Scenario</legend>
                {catalogue.data.scenarios.map((s) => (
                  <label key={s.id} className="rehearsal-choice">
                    <input
                      type="radio"
                      name="reh-scenario"
                      value={s.id}
                      checked={scenarioId === s.id}
                      onChange={() => { setScenarioId(s.id); setAcknowledged(false); setTyped(""); }}
                    />
                    <span>
                      <span>{s.title}</span>
                      <span className="dim small">{s.summary}</span>
                    </span>
                  </label>
                ))}
              </fieldset>
            )}
          </div>
        </section>
      </div>

      <section className="widget rehearsal-panel" aria-labelledby="reh-check">
        <div className="widget-head"><h2 id="reh-check">3 · Safety check and run</h2></div>
        <div className="widget-body">
          {!target || !scenario ? (
            <p className="dim">Choose an authorised host and a scenario to see what the rehearsal will affect.</p>
          ) : (
            <>
              <dl className="kv">
                <dt>Target</dt><dd className="mono">{target.host_id}</dd>
                <dt>Affects</dt><dd>{scenario.affects}</dd>
                <dt>Measured</dt><dd>{scenario.metric_label} utilisation <span className="mono small">({scenario.metric})</span></dd>
                <dt>Expected duration</dt>
                <dd>{formatSeconds(scenario.expected_duration_seconds)} ({scenario.planned_envelopes} envelopes, one every {scenario.interval_seconds} s)</dd>
                <dt>Stopping</dt><dd>You can stop it at any time. A stop ends the fault with recovery telemetry before the rehearsal is judged.</dd>
                <dt>Safety</dt><dd>Synthetic hosts only. Live and replayed hosts are refused by the server, not just hidden here.</dd>
              </dl>
              {running && <p className="rehearsal-refusal">Another rehearsal is running, so this one cannot start yet.</p>}
              <div className="rehearsal-confirm">
                <label className="rehearsal-check">
                  <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
                  <span>I confirm this is an authorised test host and that this scenario may be sent as telemetry for it.</span>
                </label>
                <label className="field rehearsal-typed">
                  <span>Type the host id <span className="mono">{target.host_id}</span> to confirm</span>
                  <input
                    value={typed}
                    onChange={(e) => setTyped(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    aria-describedby="reh-typed-hint"
                  />
                </label>
                <span id="reh-typed-hint" className="dim small">The id must match exactly.</span>
                <button type="button" className="btn primary" disabled={!canRun} onClick={run}>
                  {m.start.isPending ? "Starting…" : "Start rehearsal"}
                </button>
              </div>
              {m.start.isError && <div className="banner warn" role="alert">{rehearsalFailure(m.start.error)}</div>}
            </>
          )}
        </div>
      </section>

      <section aria-labelledby="reh-recent" className="rehearsal-recent">
        <h2 id="reh-recent" className="section-title">Recent rehearsals</h2>
        {list.isPending && <Loading label="Loading rehearsals" />}
        {list.isError && <ErrorState error={list.error} onRetry={() => void list.refetch()} />}
        {list.data && list.data.rehearsals.length === 0 && (
          <EmptyState title="No rehearsals yet" body="Running and finished rehearsals are listed here, each with its own result." />
        )}
        {list.data && list.data.rehearsals.length > 0 && (
          <div className="widget table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Started</th><th>Host</th><th>Scenario</th><th>Execution</th><th>Detection</th><th>Delay</th>
                  <th><span className="sr-only">Open</span></th>
                </tr>
              </thead>
              <tbody>
                {list.data.rehearsals.map((r) => (
                  <tr key={r.id}>
                    <td className="mono small" title={r.started_at ?? r.created_at}>{timestamp(r.started_at ?? r.created_at)}</td>
                    <td className="mono">{r.host_id}</td>
                    <td>{r.scenario}</td>
                    <td><span className={`verdict verdict-${executionTone(r.execution)}`}>{EXECUTION_LABEL[r.execution]}</span></td>
                    <td>
                      {r.detection ? (
                        <span className={`verdict verdict-${detectionTone(r.detection)}`}>{DETECTION_LABEL[r.detection]}</span>
                      ) : DASH}
                    </td>
                    <td className="mono small">{r.detection_delay_seconds === null ? DASH : formatSeconds(r.detection_delay_seconds)}</td>
                    <td>
                      <Link className="btn" to={`/rehearsal/${encodeURIComponent(r.id)}`} aria-label={`Open rehearsal on ${r.host_id}`}>Open</Link>
                    </td>
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
