import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import { useCredentials, useMonitoringSettings, useRegistryHosts, useRegistryMutations } from "../../api/hooks";
import type { IssuedCredential, RegistryHost, RegistryHostKind } from "../../api/types";
import { apiBaseUrl } from "../../app/config";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { StaleBanner } from "../../components/ui/Banners";
import { ago, timestamp } from "../../lib/format";
import {
  DEFAULT_INTERVAL_SECONDS, deriveStep, installCommands, validateRegistration, verifyState, type OnboardingStep, type RegistrationDraft, type VerifyState,
} from "../../lib/onboarding";
import { thresholdText, ruleFor } from "../../lib/hostDetail";
import { RULE_METRICS } from "../../lib/monitoringSettings";

const VERIFY_POLL_MS = 5_000;
const STEPS: { key: OnboardingStep; label: string }[] = [
  { key: "register", label: "Register the host" },
  { key: "credential", label: "Create its agent credential" },
  { key: "install", label: "Install and configure the agent" },
  { key: "verify", label: "Verify the first telemetry" },
  { key: "connected", label: "Connected" },
];
const ORDER: OnboardingStep[] = STEPS.map((s) => s.key);

function failureText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.isNetwork) return "Could not reach the UptimeTrace API. Nothing was changed.";
    if (e.status === 409) return "A host with that id already exists. Choose another id, or continue with the existing host.";
    const detail = e.details.map((d) => `${d.path}: ${d.message}`).join(" ");
    return `${e.message || "The request failed."}${detail ? ` ${detail}` : ""}`.trim();
  }
  return "The request failed. Nothing was changed.";
}

export function OnboardingPage() {
  const [params, setParams] = useSearchParams();
  const hostId = params.get("host");
  // A user with hosts who chooses to register another one is taken to the form, not back to the same screen.
  const [addingAnother, setAddingAnother] = useState(false);
  // Polling is scoped to the verify panel below; this query is a single read, not a poll.
  const hosts = useRegistryHosts({ refetchInterval: false });
  const host = hostId ? hosts.data?.find((h) => h.host_id === hostId) : undefined;

  const goTo = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set("host", id);
    else next.delete("host");
    setParams(next, { replace: false });
  };

  return (
    <>
      <PageHeader title="Set up UptimeTrace" subtitle="Connect your first host. Every step is checked against the server; nothing is marked done until telemetry arrives." />
      {hosts.isPending && <Loading label="Checking the workspace" />}
      {hosts.isError && !hosts.data && <ErrorState error={hosts.error} onRetry={() => hosts.refetch()} />}
      {hosts.data && (
        <>
          <StaleBanner failed={hosts.isError} updatedAt={hosts.dataUpdatedAt} onRetry={() => hosts.refetch()} />
          {hosts.data.length > 0 && !hostId && !addingAnother ? (
            <ExistingWorkspace count={hosts.data.length} onRegister={() => setAddingAnother(true)} />
          ) : (
            <Flow hostId={hostId} host={host} known={hosts.data} onSelect={(id) => { setAddingAnother(false); goTo(id); }} />
          )}
        </>
      )}
    </>
  );
}

/** A workspace that already has hosts is not put through first-time setup again. */
function ExistingWorkspace({ count, onRegister }: { count: number; onRegister: () => void }) {
  return (
    <section className="widget" aria-label="Workspace hosts">
      <div className="widget-head"><h2>Your hosts</h2><span className="dim mono">{count} registered</span></div>
      <div className="widget-body">
        <p>Your workspace already has hosts, so setup is not required. Register another host whenever you need one.</p>
        <div className="row-actions">
          <Link className="btn" to="/hosts">View hosts</Link>
          <Link className="btn" to="/">Command Center</Link>
          <button type="button" className="btn" onClick={onRegister}>Register another host</button>
        </div>
      </div>
    </section>
  );
}

function Flow({ hostId, host, known, onSelect }: {
  hostId: string | null; host: RegistryHost | undefined; known: RegistryHost[]; onSelect: (id: string | null) => void;
}) {
  if (hostId && !host) {
    return (
      <EmptyState
        title="That host is not registered"
        body="The host in this link is not in the registry. It may have been removed. Register a host to start again."
      >
        <button type="button" className="btn" onClick={() => onSelect(null)}>Register a host</button>
      </EmptyState>
    );
  }
  if (!host) {
    return (
      <>
        <Progress current="register" />
        <RegisterStep known={known} onRegistered={(id) => onSelect(id)} />
      </>
    );
  }
  return <HostFlow host={host} />;
}

function Progress({ current }: { current: OnboardingStep }) {
  const idx = ORDER.indexOf(current);
  return (
    <ol className="onboard-progress" aria-label="Setup progress">
      {STEPS.map((s, i) => {
        const state = i < idx ? "done" : i === idx ? "current" : "pending";
        return (
          <li key={s.key} className={`onboard-step onboard-${state}`} aria-current={state === "current" ? "step" : undefined}>
            <span className="mono small">{state === "done" ? "✓" : i + 1}</span> {s.label}
          </li>
        );
      })}
    </ol>
  );
}

function RegisterStep({ known, onRegistered }: { known: RegistryHost[]; onRegistered: (id: string) => void }) {
  const [draft, setDraft] = useState<RegistrationDraft>({ hostId: "", kind: "live", displayName: "" });
  const [touched, setTouched] = useState(false);
  const m = useRegistryMutations();
  const v = validateRegistration(draft);
  const errors = touched ? v.errors : {};
  const duplicate = known.some((h) => h.host_id === draft.hostId.trim());

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!v.valid || m.register.isPending) return;
    const id = draft.hostId.trim();
    m.register.mutate(
      { host_id: id, kind: draft.kind, ...(draft.displayName.trim() ? { display_name: draft.displayName.trim() } : {}) },
      { onSuccess: () => onRegistered(id) },
    );
  };

  return (
    <section className="widget" aria-label="Register a host">
      <div className="widget-head"><h2>1. Register the host</h2></div>
      <form className="widget-body" onSubmit={submit} noValidate>
        <p className="small">
          Registration gives this machine an id in UptimeTrace. The agent must be configured with the same id. Registering does not mean the host is reporting.
          Required: a host id. Optional: a display name. Environment and other metadata are not collected at registration.
        </p>
        <div className="field">
          <label htmlFor="host-id">Host id</label>
          <input id="host-id" value={draft.hostId} aria-invalid={!!errors.hostId} placeholder="e.g. web-01"
            onChange={(e) => { setTouched(true); setDraft({ ...draft, hostId: e.target.value }); }} />
        </div>
        <FieldError msg={errors.hostId ?? (duplicate && touched ? "A host with that id already exists. Choose another id, or continue with the existing host." : undefined)} />
        <div className="field">
          <label htmlFor="host-kind">Kind</label>
          <select id="host-kind" value={draft.kind} onChange={(e) => { setTouched(true); setDraft({ ...draft, kind: e.target.value as RegistryHostKind }); }}>
            <option value="live">Live: a real machine you run the agent on</option>
            <option value="synthetic">Synthetic: a simulator host (id starts with sim)</option>
            <option value="replay">Replay: stored history (id starts with replay-)</option>
          </select>
        </div>
        <FieldError msg={errors.kind} />
        <div className="field">
          <label htmlFor="host-name">Display name (optional)</label>
          <input id="host-name" value={draft.displayName} aria-invalid={!!errors.displayName}
            onChange={(e) => { setTouched(true); setDraft({ ...draft, displayName: e.target.value }); }} />
        </div>
        <FieldError msg={errors.displayName} />
        {m.register.isError && <p className="banner warn" role="alert">{failureText(m.register.error)}</p>}
        <div className="row-actions">
          <button type="submit" className="btn" disabled={m.register.isPending}>{m.register.isPending ? "Registering…" : "Register host"}</button>
        </div>
      </form>
    </section>
  );
}

function FieldError({ msg }: { msg?: string }) {
  return msg ? <div className="field-error small" role="alert">{msg}</div> : null;
}

/** The steps for one registered host. Each step's content comes from the server state of that host. */
function HostFlow({ host }: { host: RegistryHost }) {
  const creds = useCredentials(host.host_id);
  const activeList = creds.data?.filter((c) => !c.revoked_at);
  const step = deriveStep(host, activeList?.length);
  if (step === "connected") return <Connected host={host} />;
  return (
    <>
      <Progress current={step} />
      <RegisteredSummary host={host} />
      <CredentialStep host={host} creds={creds} />
      <InstallStep host={host} agentId={activeList?.[0]?.agent_id ?? `agent-${host.host_id}`.slice(0, 128)} />
      <VerifyStep hostId={host.host_id} />
      <ThresholdsReview />
    </>
  );
}

function RegisteredSummary({ host }: { host: RegistryHost }) {
  return (
    <section className="widget" aria-label="Registered host">
      <div className="widget-head"><h2>Registered host</h2><span className="dim small">registered {timestamp(host.created_at)}</span></div>
      <div className="widget-body">
        <dl className="kv">
          <dt>Host id</dt><dd className="mono">{host.host_id}</dd>
          <dt>Kind</dt><dd>{host.kind}</dd>
          {host.display_name && <><dt>Name</dt><dd>{host.display_name}</dd></>}
          <dt>Agent telemetry</dt><dd>{host.last_envelope_at ? `last received ${timestamp(host.last_envelope_at)}` : "Not received yet"}</dd>
        </dl>
      </div>
    </section>
  );
}

/** Credential creation. The secret is shown from component state, once, and is never put in the URL or any storage. */
function CredentialStep({ host, creds }: { host: RegistryHost; creds: ReturnType<typeof useCredentials> }) {
  const m = useRegistryMutations();
  const [issued, setIssued] = useState<IssuedCredential | null>(null);
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);
  const active = creds.data?.filter((c) => !c.revoked_at) ?? [];
  // Until the credential list has arrived we do not know whether to offer creation, so show loading, not a create button.
  if (creds.isPending) {
    return (
      <section className="widget" aria-label="Agent credential">
        <div className="widget-head"><h2>2. Agent credential</h2></div>
        <div className="widget-body"><Loading label="Checking this host's credentials" /></div>
      </section>
    );
  }

  const create = () => {
    m.issue.mutate({ hostId: host.host_id, body: {} }, {
      onSuccess: (c) => { setIssued(c); setCopied(null); m.issue.reset(); },
    });
  };
  const copy = async () => {
    if (!issued) return;
    try { await navigator.clipboard.writeText(issued.token); setCopied("yes"); } catch { setCopied("no"); }
  };

  return (
    <section className="widget" aria-label="Agent credential">
      <div className="widget-head"><h2>2. Agent credential</h2></div>
      <div className="widget-body">
        {issued ? (
          <section className="token-once" role="region" aria-label="New agent credential">
            <p className="banner warn" role="alert">
              This credential is a secret. It is shown once and cannot be retrieved later. If you leave this page before copying it, revoke it and create a new one.
            </p>
            <div className="tokenline">
              <code className="mono" data-testid="issued-credential">{issued.token}</code>
              <button type="button" className="btn" onClick={copy}>{copied === "yes" ? "Copied" : "Copy credential"}</button>
            </div>
            {copied === "no" && <p className="dim small">Copying was blocked by the browser. Select the credential and copy it manually.</p>}
            <p className="small">Agent id: <span className="mono">{issued.agent_id}</span>. Use it with the installation below.</p>
            <button type="button" className="btn" onClick={() => setIssued(null)}>I have stored the credential. Hide it.</button>
          </section>
        ) : active.length > 0 ? (
          <>
            <p className="small">
              This host has {active.length} active credential{active.length === 1 ? "" : "s"}. Their secrets were shown when they were created and cannot be retrieved. If you do not
              have one saved, revoke it and create a new one.
            </p>
            <ul className="plain small">
              {active.map((c) => (
                <li key={c.id}>
                  <span className="mono">{c.name}</span> · agent <span className="mono">{c.agent_id}</span> · created {timestamp(c.created_at)}{" "}
                  <button type="button" className="btn" onClick={() => m.revoke.mutate({ hostId: host.host_id, credentialId: c.id })}>Revoke</button>
                </li>
              ))}
            </ul>
            <button type="button" className="btn" disabled={m.issue.isPending} onClick={create}>Create a new credential</button>
          </>
        ) : (
          <>
            <p className="small">The agent authenticates to UptimeTrace with a credential that belongs to this host. Create one now.</p>
            <button type="button" className="btn" disabled={m.issue.isPending} onClick={create}>{m.issue.isPending ? "Creating…" : "Create agent credential"}</button>
          </>
        )}
        {m.issue.isError && <p className="banner warn" role="alert">Could not create the credential. Nothing was created.</p>}
        {creds.isError && !creds.data && <ErrorState error={creds.error} onRetry={() => creds.refetch()} />}
      </div>
    </section>
  );
}

/** Installation for the Go agent as it exists in this repository: build from source, configure with environment variables. */
function InstallStep({ host, agentId }: { host: RegistryHost; agentId: string }) {
  const url = apiBaseUrl || window.location.origin;
  const cmds = installCommands({ url, hostId: host.host_id, agentId });
  return (
    <section className="widget" aria-label="Install the agent">
      <div className="widget-head"><h2>3. Install and configure the agent</h2></div>
      <div className="widget-body">
        <p className="small">
          There is no installer. Build the agent from this repository, then run it on the machine with the values below. Windows is the verified platform. Linux
          collection exists but has not been run on a real Linux host.
        </p>
        <ol className="plain small">
          <li>Build the agent (requires Go).</li>
          <li>Run it with the configuration below, replacing <span className="mono">&lt;token&gt;</span> with the credential from step 2.</li>
          <li>Keep the credential out of shared files and shell history where you can.</li>
        </ol>
        <h3 className="small">Build</h3>
        <CopyBlock text={cmds.build} label="build command" />
        <h3 className="small">Run on Windows (PowerShell)</h3>
        <CopyBlock text={cmds.windows} label="Windows command" />
        <h3 className="small">Run on Linux or macOS</h3>
        <CopyBlock text={cmds.unix} label="shell command" />
        <p className="dim small">
          The agent id must match the credential's agent id. The agent sends telemetry every {DEFAULT_INTERVAL_SECONDS} seconds by default; set
          <span className="mono"> UPTIMETRACE_INTERVAL </span> to change it. Values shown here are not secrets: only <span className="mono">&lt;token&gt;</span> is.
        </p>
      </div>
    </section>
  );
}

function CopyBlock({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "copied" | "blocked">("idle");
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setState("copied"); } catch { setState("blocked"); }
  };
  return (
    <div className="copy-block">
      <pre className="mono small snippet" aria-label={label}>{text}</pre>
      <button type="button" className="btn" onClick={copy}>{state === "copied" ? "Copied" : `Copy ${label}`}</button>
      {state === "blocked" && <span className="dim small"> Copying was blocked. Select the text and copy it manually.</span>}
    </div>
  );
}

/** First-telemetry verification. Polls the registry while mounted; unmounting (leaving the step or the page) stops polling. */
function VerifyStep({ hostId }: { hostId: string }) {
  const q = useRegistryHosts({ refetchInterval: VERIFY_POLL_MS });
  const row = q.data?.find((h) => h.host_id === hostId);
  const state: VerifyState = verifyState(row);
  const failed = q.isError;
  if (q.isError && !q.data) {
    return (
      <section className="widget" aria-label="Verify first telemetry">
        <div className="widget-body">
          <p className="banner warn" role="alert">The verification request failed, so nothing has been checked. Retry to check again.</p>
          <button type="button" className="btn" onClick={() => q.refetch()}>Retry verification</button>
        </div>
      </section>
    );
  }

  return (
    <section className="widget" aria-label="Verify first telemetry">
      <div className="widget-head"><h2>4. Verify the first telemetry</h2><span className="dim small">checked every {VERIFY_POLL_MS / 1000} s</span></div>
      <div className="widget-body" role="status" aria-live="polite">
        {failed && <p className="banner warn" role="alert">The verification request failed, so nothing has been checked. It will retry automatically.</p>}
        {state === "awaiting_first_telemetry" && (
          <>
            <p><b>Registered. Waiting for the agent's first telemetry.</b> Registration alone does not mean the agent is reporting.</p>
            <Troubleshooting />
          </>
        )}
        {state === "stale" && (
          <p><b>Telemetry is late.</b> The last report was {row?.last_envelope_at ? `${timestamp(row.last_envelope_at)}` : "earlier"}. The agent may have stopped, or the network may be slow. This is not a confirmed failure yet.</p>
        )}
        {state === "silent" && (
          <>
            <p><b>No telemetry for longer than the offline timeout.</b> The host is offline. The agent, the network or the machine may be the cause; UptimeTrace cannot tell which.</p>
            <Troubleshooting />
          </>
        )}
        {state === "replay" && <p>This host replays stored history, so there is no live agent to verify.</p>}
        {state === "not_registered" && <p>This host is not in the registry.</p>}
        <div className="row-actions">
          <button type="button" className="btn" onClick={() => q.refetch()}>Check again</button>
        </div>
      </div>
    </section>
  );
}

function Troubleshooting() {
  return (
    <ul className="plain small">
      <li>Is the agent running on the machine? It stops when its terminal is closed.</li>
      <li>Is <span className="mono">UPTIMETRACE_URL</span> the address of this API, and can the machine reach it?</li>
      <li>Is <span className="mono">UPTIMETRACE_TOKEN</span> exactly the credential from step 2? A revoked or mistyped credential is refused.</li>
      <li>Does <span className="mono">UPTIMETRACE_HOST_ID</span> match the host id, and <span className="mono">UPTIMETRACE_AGENT_ID</span> match the credential's agent id?</li>
    </ul>
  );
}

/** Saved thresholds, read from the existing monitoring settings. Changes are made in Alert Policies, not here. */
function ThresholdsReview() {
  const s = useMonitoringSettings();
  return (
    <section className="widget" aria-label="Review thresholds">
      <div className="widget-head"><h2>5. Review thresholds</h2><Link className="small" to="/settings/alerts">Change them in Alert Policies</Link></div>
      <div className="widget-body">
        {s.isPending && <Loading label="Loading thresholds" />}
        {s.isError && !s.data && <ErrorState error={s.error} onRetry={() => s.refetch()} />}
        {s.data && (
          <>
            <p className="small">{s.data.version === 0 ? "Built-in defaults are in effect." : `Saved version ${s.data.version}.`}</p>
            <ul className="plain small">
              {RULE_METRICS.map((m) => (
                <li key={m.key}>{m.label}: {thresholdText(ruleFor(s.data!.config.rules, m.key)) ?? "no limit configured"}</li>
              ))}
              <li>Stale after {s.data.config.freshness.stale_after_seconds} s; offline after {s.data.config.freshness.offline_after_seconds} s.</li>
            </ul>
          </>
        )}
      </div>
    </section>
  );
}

function Connected({ host }: { host: RegistryHost }) {
  return (
    <section className="widget" aria-label="Connected">
      <div className="widget-head"><h2>Connected</h2></div>
      <div className="widget-body">
        <p><b>First telemetry received</b> from <span className="mono">{host.host_id}</span>. Last received {timestamp(host.last_envelope_at!)} ({ago((Date.now() - Date.parse(host.last_envelope_at!)) / 1000)}).</p>
        <p className="small">Its state now comes from the telemetry, not from registration. Health is judged from the metrics; the agent's reporting state is shown separately.</p>
        <div className="row-actions">
          <Link className="btn" to="/">Command Center</Link>
          <Link className="btn" to={`/hosts/${encodeURIComponent(host.host_id)}`}>Open host detail</Link>
          <Link className="btn" to="/settings/alerts">Alert Policies</Link>
        </div>
      </div>
    </section>
  );
}
