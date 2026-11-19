import { useState } from "react";
import { ApiError } from "../../api/client";
import { useMonitoringSettings, useSaveMonitoringSettings } from "../../api/hooks";
import type { MonitoringSettings } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { ErrorState, Loading } from "../../components/states/States";
import { HealthBadge } from "../../components/ui/Badges";
import { timestamp } from "../../lib/format";
import {
  OFFLINE_MAX_SECONDS, RULE_METRICS, STALE_MAX_SECONDS, STALE_MIN_SECONDS, configFrom, draftFrom, isChanged, isDefault, validateDraft,
  type Draft, type RuleKey,
} from "../../lib/monitoringSettings";

export function AlertPoliciesPage() {
  const q = useMonitoringSettings();
  return (
    <>
      <PageHeader title="Alert Policies" subtitle="The thresholds and freshness windows that decide host health and when alerts open." />
      {q.isPending && <Loading label="Loading the saved policies" />}
      {q.isError && !q.data && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && <Editor settings={q.data} reload={async () => (await q.refetch()).data} />}
    </>
  );
}

function Editor({ settings, reload }: { settings: MonitoringSettings; reload: () => Promise<MonitoringSettings | undefined> }) {
  const saved = settings.config;
  const [draft, setDraft] = useState<Draft>(() => draftFrom(saved));
  const [touched, setTouched] = useState(false);
  const save = useSaveMonitoringSettings();
  const validation = validateDraft(draft);
  const changed = isChanged(draft, saved);
  const errors = touched || changed ? validation.errors : {};
  const isDefaultNow = isDefault(saved, settings.defaults);

  const set = (path: string, value: string) => {
    setTouched(true);
    setDraft((d) => {
      if (path === "stale" || path === "offline") return { ...d, [path]: value };
      const [key, field] = path.split("|") as [RuleKey, "warning" | "critical"];
      return { ...d, rules: { ...d.rules, [key]: { ...d.rules[key], [field]: value } } };
    });
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!validation.valid || !changed || save.isPending) return;
    save.mutate(
      { config: configFrom(draft), expected_version: settings.version },
      // Only a server-confirmed save replaces the form, so what is shown afterwards is the saved state.
      { onSuccess: (confirmed) => setDraft(draftFrom(confirmed.config)) },
    );
  };

  const failure = save.error instanceof ApiError ? save.error : null;
  const conflict = failure?.status === 409;

  return (
    <form onSubmit={submit} aria-label="Alert policy settings" noValidate>
      <section className="widget" aria-label="Saved policy">
        <div className="widget-body policy-status">
          {settings.version === 0 ? (
            <span>Built-in defaults are in effect. Nothing has been saved for this workspace yet.</span>
          ) : (
            <span>
              Saved version <b className="mono">{settings.version}</b>
              {settings.updated_by && <> by <span className="mono">{settings.updated_by}</span></>}
              {settings.updated_at && <> at <span className="mono">{timestamp(settings.updated_at)}</span></>}
              {isDefaultNow ? " · matches the defaults" : " · customised"}
            </span>
          )}
        </div>
      </section>

      <Explanation />

      <section className="widget" aria-labelledby="thresholds-h">
        <div className="widget-head"><h2 id="thresholds-h">Resource thresholds</h2><span className="dim small">percent of capacity, 0 to 100</span></div>
        <div className="widget-body">
          <p className="dim small">A value at or above the warning limit makes the host warning and opens a warning alert. A value at or above the critical limit makes it critical and opens a critical alert. Both are measured breaches of a resource, not missing data.</p>
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th><HealthBadge status="warning" /> limit (%)</th>
                  <th><HealthBadge status="critical" /> limit (%)</th>
                  <th>Saved</th>
                  <th>Default</th>
                </tr>
              </thead>
              <tbody>
                {RULE_METRICS.map((m) => {
                  const r = draft.rules[m.key];
                  const sv = saved.rules[m.key];
                  const dv = settings.defaults.rules[m.key];
                  return (
                    <tr key={m.key}>
                      <td>{m.label}</td>
                      <td>
                        <input type="text" inputMode="decimal" aria-label={`${m.label} warning limit (%)`} aria-invalid={!!errors[`${m.key}.warning`]}
                          value={r.warning} onChange={(e) => set(`${m.key}|warning`, e.target.value)} className="num-input" />
                        <FieldError msg={errors[`${m.key}.warning`]} />
                      </td>
                      <td>
                        <input type="text" inputMode="decimal" aria-label={`${m.label} critical limit (%)`} aria-invalid={!!errors[`${m.key}.critical`]}
                          value={r.critical} onChange={(e) => set(`${m.key}|critical`, e.target.value)} className="num-input" />
                        <FieldError msg={errors[`${m.key}.critical`]} />
                      </td>
                      <td className="mono small dim">{sv ? `${sv.warning} / ${sv.critical}` : "—"}</td>
                      <td className="mono small dim">{dv ? `${dv.warning} / ${dv.critical}` : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="dim small">Each limit is a percentage. The warning limit must be above 0 and below the critical limit, which must be at most 100.</p>
        </div>
      </section>

      <section className="widget" aria-labelledby="fresh-h">
        <div className="widget-head"><h2 id="fresh-h">Telemetry freshness</h2><span className="dim small">seconds</span></div>
        <div className="widget-body">
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr><th>Setting</th><th>Value (seconds)</th><th>Saved</th><th>Default</th></tr></thead>
              <tbody>
                <tr>
                  <td>Stale after<div className="dim small">Telemetry is late. The host is stale and its values are not judged.</div></td>
                  <td>
                    <input type="text" inputMode="numeric" aria-label="Stale timeout (seconds)" aria-invalid={!!errors.stale}
                      value={draft.stale} onChange={(e) => set("stale", e.target.value)} className="num-input" />
                    <FieldError msg={errors.stale} />
                  </td>
                  <td className="mono small dim">{saved.freshness.stale_after_seconds}</td>
                  <td className="mono small dim">{settings.defaults.freshness.stale_after_seconds}</td>
                </tr>
                <tr>
                  <td>Offline after<div className="dim small">No telemetry for this long. The host is offline and an offline alert opens.</div></td>
                  <td>
                    <input type="text" inputMode="numeric" aria-label="Offline timeout (seconds)" aria-invalid={!!errors.offline}
                      value={draft.offline} onChange={(e) => set("offline", e.target.value)} className="num-input" />
                    <FieldError msg={errors.offline} />
                  </td>
                  <td className="mono small dim">{saved.freshness.offline_after_seconds}</td>
                  <td className="mono small dim">{settings.defaults.freshness.offline_after_seconds}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="dim small">Stale timeout: {STALE_MIN_SECONDS} to {STALE_MAX_SECONDS} seconds. Offline timeout: at most {OFFLINE_MAX_SECONDS} seconds and longer than the stale timeout.</p>
        </div>
      </section>

      <section className="widget" aria-label="Save">
        <div className="widget-body policy-actions">
          {changed && !validation.valid && <p className="banner warn" role="alert">Fix the highlighted fields before saving. Nothing has been changed on the server.</p>}
          {save.isPending && <p className="dim" role="status">Saving…</p>}
          {save.isSuccess && !changed && (
            <p className="banner notice" role="status">Saved as version {save.data.version}. Hosts, Host detail and alerts were refreshed. The new limits apply at the next evaluation.</p>
          )}
          {failure && conflict && (
            <p className="banner warn" role="alert">
              These policies changed since this page loaded, so nothing was saved. Reload the saved values, check them, and apply your change again.{" "}
              <button type="button" className="btn" onClick={async () => {
                const fresh = await reload();
                if (fresh) setDraft(draftFrom(fresh.config));
                setTouched(false);
                save.reset();
              }}>Reload saved values</button>
            </p>
          )}
          {failure && !conflict && failure.isNetwork && (
            <p className="banner warn" role="alert">Could not reach the API. Nothing was saved.</p>
          )}
          {failure && !conflict && !failure.isNetwork && (
            <p className="banner warn" role="alert">
              Could not save the policies. Nothing was changed.
              {failure.details.length > 0 ? " " + failure.details.map((d) => `${d.path}: ${d.message}`).join("; ") : failure.message ? ` ${failure.message}` : ""}
            </p>
          )}
          {!failure && save.isError && (
            <p className="banner warn" role="alert">Could not save the policies. Nothing was changed.</p>
          )}
          <div className="row-actions">
            <button type="submit" className="btn" disabled={!changed || !validation.valid || save.isPending}>Save policies</button>
            <button type="button" className="btn" disabled={!changed || save.isPending} onClick={() => { setDraft(draftFrom(saved)); setTouched(false); }}>Discard edits</button>
            <button type="button" className="btn" disabled={save.isPending} onClick={() => { setDraft(draftFrom(settings.defaults)); setTouched(true); }}>Fill in defaults</button>
          </div>
          <p className="dim small">Saving changes what future evaluations decide. It does not change anything until you save, and filling in defaults only edits this form.</p>
          <p className="dim small">Lowering a limit can open new alerts at the next evaluation. Raising one lets an open alert recover when its condition clears. Existing alerts keep their state until an evaluation judges them.</p>
        </div>
      </section>
    </form>
  );
}

function FieldError({ msg }: { msg?: string }) {
  return msg ? <div className="field-error small" role="alert">{msg}</div> : null;
}

/** What each state means. Missing telemetry and measured breaches are kept apart on purpose. */
function Explanation() {
  return (
    <section className="widget" aria-label="How these policies behave">
      <div className="widget-head"><h2>How these policies behave</h2></div>
      <div className="widget-body">
        <dl className="kv policy-terms">
          <dt><HealthBadge status="warning" /></dt>
          <dd>A measured resource value is at or above the warning limit. This is a confirmed breach, and it opens a warning alert.</dd>
          <dt><HealthBadge status="critical" /></dt>
          <dd>A measured resource value is at or above the critical limit. This is a confirmed breach, and it opens a critical alert.</dd>
          <dt><HealthBadge status="stale" /></dt>
          <dd>The newest telemetry is older than the stale timeout. The values are not judged until fresh telemetry arrives. This means telemetry is late. It does not mean the machine has crashed.</dd>
          <dt><HealthBadge status="offline" /></dt>
          <dd>No telemetry for longer than the offline timeout. The host is offline, and an offline alert opens. The cause could be the agent, the network or the machine; UptimeTrace cannot tell which.</dd>
        </dl>
        <p className="dim small">
          Stale comes first and offline second: a host goes stale after the stale timeout and offline after the longer offline timeout.
          Per-sample freshness inside a report is fixed at 300 seconds and is not configurable here.
        </p>
      </div>
    </section>
  );
}
