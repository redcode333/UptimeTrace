import { useState, type FormEvent } from "react";
import { ApiError } from "../../api/client";
import { useCredentials, useRegistryHosts, useRegistryMutations } from "../../api/hooks";
import type { IssuedCredential, RegistryHost, RegistryHostKind, TelemetryState } from "../../api/types";
import { apiBaseUrl } from "../../app/config";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { SourceBadge } from "../../components/ui/Badges";
import { DASH, timestamp } from "../../lib/format";

const STATE_LABEL: Record<TelemetryState, string> = {
  awaiting_first_telemetry: "Awaiting first telemetry",
  reporting: "Reporting",
  stale: "Stale",
  silent: "Silent (offline)",
  replay: "Replay (not live)",
};

/** A failed operation, in words. Field-level problems from the API are listed; nothing about the request body is echoed. */
function failureText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.isNetwork) return "Could not reach the UptimeTrace API. Nothing was changed.";
    const detail = e.details.map((d) => `${d.path}: ${d.message}`).join(" ");
    return `${e.message || "The request failed."}${detail ? ` ${detail}` : ""}`.trim();
  }
  return "The request failed. Nothing was changed.";
}

export function AgentsPage() {
  const hosts = useRegistryHosts();
  const m = useRegistryMutations();
  const [selected, setSelected] = useState<string | null>(null);
  const [form, setForm] = useState<{ host_id: string; kind: RegistryHostKind; display_name: string }>({ host_id: "", kind: "live", display_name: "" });
  // The plaintext token lives only here. It is never persisted, never put in a query cache, and is dropped on dismiss.
  const [issued, setIssued] = useState<{ hostId: string; agentId: string; cred: IssuedCredential } | null>(null);

  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    m.register.mutate(
      { host_id: form.host_id.trim(), kind: form.kind, ...(form.display_name.trim() ? { display_name: form.display_name.trim() } : {}) },
      { onSuccess: (h) => { setForm({ host_id: "", kind: form.kind, display_name: "" }); setSelected(h.host_id); } },
    );
  };

  return (
    <>
      <PageHeader title="Agents" subtitle="Register hosts and manage the credentials their agents use to send telemetry" />

      <form className="toolbar agents-form" onSubmit={submit} aria-label="Register a host">
        <label className="field">
          <span className="dim">Host id</span>
          <input required value={form.host_id} onChange={(e) => setForm({ ...form, host_id: e.target.value })} placeholder="e.g. web-01" pattern="[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}" title="Letters, digits, . _ : - (max 128), starting with a letter or digit" />
        </label>
        <label className="field">
          <span className="dim">Kind</span>
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as RegistryHostKind })}>
            <option value="live">Live (real infrastructure)</option>
            <option value="synthetic">Synthetic (host id must start with sim-)</option>
            <option value="replay">Replay (host id must start with replay-)</option>
          </select>
        </label>
        <label className="field">
          <span className="dim">Display name</span>
          <input value={form.display_name} maxLength={253} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="optional" />
        </label>
        <button type="submit" className="btn" disabled={m.register.isPending || !form.host_id.trim()}>{m.register.isPending ? "Registering…" : "Register host"}</button>
      </form>
      {m.register.isError && <div className="banner warn" role="alert">Could not register the host. {failureText(m.register.error)}</div>}

      {hosts.isPending && <Loading label="Loading registered hosts" />}
      {hosts.isError && !hosts.data && <ErrorState error={hosts.error} onRetry={() => hosts.refetch()} />}
      {hosts.data && (hosts.data.length === 0 ? (
        <EmptyState title="No hosts registered yet" body="Register a host above, then issue it a credential and start the agent with that credential. The host appears in Hosts after its first telemetry." />
      ) : (
        <div className="widget table-wrap">
          <table className="tbl">
            <thead><tr><th>Host</th><th>Kind</th><th>Telemetry</th><th>Last telemetry</th><th className="num">Active credentials</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {hosts.data.map((h: RegistryHost) => (
                <tr key={h.host_id} className={selected === h.host_id ? "row-selected" : undefined}>
                  <td><span className="mono">{h.host_id}</span>{h.display_name && <div className="dim small">{h.display_name}</div>}</td>
                  <td><SourceBadge source={h.kind} /></td>
                  <td>{STATE_LABEL[h.telemetry_state]}</td>
                  <td className="mono small" title={h.last_envelope_at ?? undefined}>{h.last_envelope_at ? timestamp(h.last_envelope_at) : DASH}</td>
                  <td className="num mono">{h.active_credentials}</td>
                  <td>
                    <button type="button" className="btn" aria-expanded={selected === h.host_id} aria-label={`Credentials for ${h.host_id}`}
                      onClick={() => setSelected(selected === h.host_id ? null : h.host_id)}>
                      {selected === h.host_id ? "Hide credentials" : "Credentials"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}

      {selected && (
        <CredentialsPanel
          hostId={selected}
          issued={issued && issued.hostId === selected ? issued : null}
          onIssued={(agentId, cred) => setIssued({ hostId: selected, agentId, cred })}
          onDismissToken={() => setIssued(null)}
          mutations={m}
        />
      )}
    </>
  );
}

function CredentialsPanel({ hostId, issued, onIssued, onDismissToken, mutations: m }: {
  hostId: string;
  issued: { agentId: string; cred: IssuedCredential } | null;
  onIssued: (agentId: string, c: IssuedCredential) => void;
  onDismissToken: () => void;
  mutations: ReturnType<typeof useRegistryMutations>;
}) {
  const creds = useCredentials(hostId);
  const [name, setName] = useState("");
  const [agentId, setAgentId] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);

  const issue = (ev: FormEvent) => {
    ev.preventDefault();
    m.issue.mutate(
      { hostId, body: { ...(name.trim() ? { name: name.trim() } : {}), ...(agentId.trim() ? { agent_id: agentId.trim() } : {}) } },
      { onSuccess: (c) => { onIssued(c.agent_id, c); setName(""); setAgentId(""); } },
    );
  };

  return (
    <section className="widget" aria-label={`Credentials for ${hostId}`}>
      <div className="widget-head"><h2>Credentials · <span className="mono">{hostId}</span></h2></div>
      <div className="widget-body stack-gap">
        {issued && <TokenReveal issued={issued} hostId={hostId} onDismiss={onDismissToken} />}

        <form className="toolbar" onSubmit={issue} aria-label="Issue a credential">
          <label className="field"><span className="dim">Name</span><input value={name} maxLength={64} onChange={(e) => setName(e.target.value)} placeholder="optional, e.g. web-01-agent" /></label>
          <label className="field"><span className="dim">Agent id</span><input value={agentId} maxLength={128} onChange={(e) => setAgentId(e.target.value)} placeholder={`default: agent-${hostId}`} /></label>
          <button type="submit" className="btn" disabled={m.issue.isPending}>{m.issue.isPending ? "Issuing…" : "Issue credential"}</button>
        </form>
        {m.issue.isError && <div className="banner warn" role="alert">Could not issue a credential. {failureText(m.issue.error)}</div>}
        {m.revoke.isError && <div className="banner warn" role="alert">Could not revoke the credential. {failureText(m.revoke.error)}</div>}

        {creds.isPending && <Loading label="Loading credentials" />}
        {creds.isError && <ErrorState error={creds.error} onRetry={() => creds.refetch()} />}
        {creds.data && (creds.data.length === 0 ? (
          <p className="dim">No credentials yet. Issue one to let an agent send telemetry for this host.</p>
        ) : (
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr><th>Name</th><th>Agent id</th><th>Created</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {creds.data.map((c) => (
                  <tr key={c.id}>
                    <td className="mono">{c.name}</td>
                    <td className="mono small">{c.agent_id}</td>
                    <td className="mono small" title={c.created_at}>{timestamp(c.created_at)}<div className="dim">{c.created_by}</div></td>
                    <td>{c.revoked_at ? <><span className="hb hb-offline"><i aria-hidden="true">○</i>REVOKED</span><div className="dim small mono">{timestamp(c.revoked_at)}</div></> : <span className="hb hb-healthy"><i aria-hidden="true">●</i>ACTIVE</span>}</td>
                    <td>
                      {!c.revoked_at && (confirming === c.id ? (
                        <span className="confirm" role="group" aria-label={`Confirm revoking ${c.name}`}>
                          <span>Revoke <b className="mono">{c.name}</b>? The agent using it is rejected on its next request. This cannot be undone.</span>{" "}
                          <button type="button" className="btn danger" disabled={m.revoke.isPending}
                            onClick={() => m.revoke.mutate({ hostId, credentialId: c.id }, { onSettled: () => setConfirming(null) })}>
                            {m.revoke.isPending ? "Revoking…" : "Confirm revoke"}
                          </button>{" "}
                          <button type="button" className="btn" onClick={() => setConfirming(null)}>Cancel</button>
                        </span>
                      ) : (
                        <button type="button" className="btn" aria-label={`Revoke ${c.name}`} onClick={() => setConfirming(c.id)}>Revoke</button>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </section>
  );
}

function TokenReveal({ issued, hostId, onDismiss }: { issued: { agentId: string; cred: IssuedCredential }; hostId: string; onDismiss: () => void }) {
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);
  const url = apiBaseUrl || window.location.origin;
  const copy = async () => {
    try { await navigator.clipboard.writeText(issued.cred.token); setCopied("yes"); } catch { setCopied("no"); }
  };
  return (
    <div className="tokenbox" role="alert" aria-label="New credential token">
      <strong>Copy this token now. It is shown only once and cannot be recovered.</strong>
      <p className="dim small">Credential <span className="mono">{issued.cred.name}</span> for agent <span className="mono">{issued.agentId}</span>. If you lose it, revoke it and issue a new one.</p>
      <div className="tokenline"><code className="mono" data-testid="issued-token">{issued.cred.token}</code>
        <button type="button" className="btn" onClick={copy}>{copied === "yes" ? "Copied" : "Copy token"}</button></div>
      {copied === "no" && <p className="dim small">Copying was blocked by the browser. Select the token and copy it manually.</p>}
      <p className="dim small">Start the agent with:</p>
      <pre className="mono small snippet">{`UPTIMETRACE_URL=${url} UPTIMETRACE_TOKEN=<token above> agent -host-id ${hostId} -agent-id ${issued.agentId}`}</pre>
      <button type="button" className="btn" onClick={onDismiss}>I have stored the token — hide it</button>
    </div>
  );
}
