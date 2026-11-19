import { useState } from "react";
import { ApiError } from "../../api/client";
import { useIssueMcpToken, useMcpTokens, useRevokeMcpToken } from "../../api/hooks";
import type { IssuedMcpToken, McpToken } from "../../api/types";
import { apiBaseUrl } from "../../app/config";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { StaleBanner } from "../../components/ui/Banners";
import { timestamp } from "../../lib/format";
import {
  MAX_ACTIVE_TOKENS, MCP_SCOPES, SCOPE_DESCRIPTION, TTL_DEFAULT_DAYS, TTL_MAX_DAYS, activeCount, tokenStatus, validateTokenDraft,
  type McpScope, type TokenDraft,
} from "../../lib/mcpTokens";

/** The endpoint this console's API serves MCP on, when the operator has enabled it. */
export function mcpEndpoint(): string {
  return `${apiBaseUrl || window.location.origin}/mcp/`;
}

const PYTHON_SNIPPET = `import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = "<endpoint>"          # the endpoint shown above
TOKEN = "<token>"           # your token; keep it out of source control

async def main():
    async with streamablehttp_client(URL, headers={"Authorization": f"Bearer {TOKEN}"}) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(sorted(t.name for t in tools.tools))

asyncio.run(main())`;

const CLIENT_CONFIG_SNIPPET = `{
  "mcpServers": {
    "uptimetrace": {
      "type": "http",
      "url": "<endpoint>",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}`;

export function IntegrationsPage() {
  const tokens = useMcpTokens();
  const [issued, setIssued] = useState<IssuedMcpToken | null>(null);
  const endpoint = mcpEndpoint();
  return (
    <>
      <PageHeader title="Integrations" subtitle="Connect an external AI client to read UptimeTrace through MCP. The connection is read-only." />
      <Overview endpoint={endpoint} />
      <section className="widget" aria-label="Access tokens">
        <div className="widget-head">
          <h2>Access tokens</h2>
          {tokens.data && <span className="dim mono">{activeCount(tokens.data, Date.now())} of {MAX_ACTIVE_TOKENS} active</span>}
        </div>
        <div className="widget-body">
          {issued && <OneTimeToken token={issued} onDismiss={() => setIssued(null)} />}
          <IssueForm onIssued={setIssued} />
          <TokenList query={tokens} />
        </div>
      </section>
      <ClientSetup endpoint={endpoint} />
    </>
  );
}

function Overview({ endpoint }: { endpoint: string }) {
  return (
    <section className="widget" aria-label="MCP access overview">
      <div className="widget-head"><h2>MCP access</h2><span className="dim small">read-only</span></div>
      <div className="widget-body">
        <p className="small">
          The API does not report whether MCP is enabled on this deployment. You can create tokens here, but a client can connect only where the operator
          has enabled the MCP endpoint. If the endpoint is not enabled, connections fail regardless of the token.
        </p>
        <dl className="kv">
          <dt>Endpoint</dt><dd className="mono">{endpoint}</dd>
          <dt>Transport</dt><dd>Streamable HTTP, JSON responses, stateless</dd>
          <dt>Authentication</dt><dd>Static bearer token in the <span className="mono">Authorization</span> header</dd>
          <dt>Access</dt><dd>Read-only. Hosts registered in this workspace, and their health and metrics.</dd>
          <dt>Rate limit</dt><dd>60 tool calls per minute per token</dd>
          <dt>Token lifetime</dt><dd>1 to {TTL_MAX_DAYS} days (default {TTL_DEFAULT_DAYS}); revocation takes effect on the next call</dd>
        </dl>
        <h3 className="small">Tools a client can call</h3>
        <table className="tbl">
          <thead><tr><th>Tool</th><th>Scope</th><th>What it returns</th></tr></thead>
          <tbody>
            <tr><td className="mono">list_hosts</td><td className="mono small">hosts:read</td><td className="small">Registered hosts with health, freshness and latest utilisation. Hosts that never reported are shown as unknown, never healthy.</td></tr>
            <tr><td className="mono">get_host_health</td><td className="mono small">hosts:read</td><td className="small">One host's health with the evidence (metric, value, threshold) and its latest samples.</td></tr>
            <tr><td className="mono">get_host_metrics</td><td className="mono small">metrics:read</td><td className="small">A bounded history of one metric (at most 7 days and 500 points). Gaps are null, never zero.</td></tr>
          </tbody>
        </table>
        <p className="dim small">
          Clients can read this data. They cannot change configuration, send telemetry, run commands or act on infrastructure. There is no MCP tool for alerts, incidents or remediation.
        </p>
        <h3 className="small">Limitations</h3>
        <ul className="plain small">
          <li>Only static bearer tokens. OAuth sign-in and hosted connector discovery are not implemented, so clients that require them cannot connect.</li>
          <li>Each token carries only the scopes it was issued with. A token can read only the hosts in this workspace.</li>
          <li>Tokens are not retrievable after creation. A lost token must be revoked and replaced.</li>
          <li>The rate limit is kept per server process, so with several API workers the effective limit is higher.</li>
        </ul>
      </div>
    </section>
  );
}

/** The raw token: shown once, here, from component state only. Not in the URL, not in any cache, not logged. */
function OneTimeToken({ token, onDismiss }: { token: IssuedMcpToken; onDismiss: () => void }) {
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(token.token);
      setCopied("yes");
    } catch {
      setCopied("no");
    }
  };
  return (
    <section className="token-once" role="region" aria-label="New access token">
      <p className="banner warn" role="alert">
        Treat this token as a secret. It is shown once and cannot be retrieved again. Anyone with it can read what <b>{token.name}</b> is allowed to read until it is revoked or expires.
      </p>
      <div className="tokenline">
        <code className="mono" data-testid="issued-token">{token.token}</code>
        <button type="button" className="btn" onClick={copy}>{copied === "yes" ? "Copied" : "Copy token"}</button>
      </div>
      {copied === "no" && <p className="dim small">Copying was blocked by the browser. Select the token and copy it manually.</p>}
      <button type="button" className="btn" onClick={onDismiss}>I have stored the token. Hide it.</button>
    </section>
  );
}

function IssueForm({ onIssued }: { onIssued: (t: IssuedMcpToken) => void }) {
  const [draft, setDraft] = useState<TokenDraft>({ name: "", scopes: [...MCP_SCOPES], days: String(TTL_DEFAULT_DAYS) });
  const [touched, setTouched] = useState(false);
  const issue = useIssueMcpToken();
  const v = validateTokenDraft(draft);
  const errors = touched ? v.errors : {};

  const toggle = (scope: McpScope) => {
    setTouched(true);
    setDraft((d) => ({ ...d, scopes: d.scopes.includes(scope) ? d.scopes.filter((s) => s !== scope) : [...d.scopes, scope] }));
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!v.valid || issue.isPending) return;
    issue.mutate(
      { name: draft.name.trim(), scopes: draft.scopes, expires_in_days: Number(draft.days) },
      {
        onSuccess: (t) => {
          onIssued(t);
          setDraft((d) => ({ ...d, name: "" }));
          setTouched(false);
          issue.reset(); // do not keep the raw token in the mutation's state
        },
      },
    );
  };

  const failure = issue.error instanceof ApiError ? issue.error : null;
  return (
    <form onSubmit={submit} aria-label="Create an MCP token" noValidate className="issue-form">
      <h3 className="small">Create a token</h3>
      <div className="field">
        <label htmlFor="mcp-name">Name</label>
        <input id="mcp-name" value={draft.name} aria-invalid={!!errors.name} placeholder="e.g. claude-desktop-laptop"
          onChange={(e) => { setTouched(true); setDraft((d) => ({ ...d, name: e.target.value })); }} />
      </div>
      <FieldError msg={errors.name} />
      <fieldset className="scope-set">
        <legend className="small">Scopes (only these are enforced)</legend>
        {MCP_SCOPES.map((s) => (
          <label key={s} className="small scope-option">
            <input type="checkbox" checked={draft.scopes.includes(s)} onChange={() => toggle(s)} />
            <span className="mono">{s}</span> {SCOPE_DESCRIPTION[s].title}, used by <span className="mono">{SCOPE_DESCRIPTION[s].tools}</span>
          </label>
        ))}
      </fieldset>
      <FieldError msg={errors.scopes} />
      <div className="field">
        <label htmlFor="mcp-days">Expires after (days)</label>
        <input id="mcp-days" inputMode="numeric" value={draft.days} aria-invalid={!!errors.days} className="num-input"
          onChange={(e) => { setTouched(true); setDraft((d) => ({ ...d, days: e.target.value })); }} />
      </div>
      <FieldError msg={errors.days} />
      <div className="row-actions">
        <button type="submit" className="btn" disabled={issue.isPending}>{issue.isPending ? "Creating…" : "Create token"}</button>
      </div>
      {failure && <p className="banner warn" role="alert">{issueFailureText(failure)}</p>}
      {issue.isError && !failure && <p className="banner warn" role="alert">Could not reach the API. No token was created.</p>}
    </form>
  );
}

/** Failure text from the server's code and details. Never includes a token, and never echoes the request. */
export function issueFailureText(e: ApiError): string {
  if (e.isNetwork) return "Could not reach the API. No token was created.";
  if (e.code === "token_limit") return `This workspace already has ${MAX_ACTIVE_TOKENS} active tokens. Revoke one before creating another.`;
  if (e.code === "token_name_taken") return "A token with that name already exists. Choose another name.";
  const detail = e.details.map((d) => `${d.path}: ${d.message}`).join("; ");
  return `Could not create the token. Nothing was created.${detail ? ` ${detail}` : ""}`;
}

function FieldError({ msg }: { msg?: string }) {
  return msg ? <div className="field-error small" role="alert">{msg}</div> : null;
}

function TokenList({ query }: { query: ReturnType<typeof useMcpTokens> }) {
  const revoke = useRevokeMcpToken();
  const [confirming, setConfirming] = useState<string | null>(null);
  if (query.isPending) return <Loading label="Loading tokens" />;
  if (query.isError && !query.data) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const rows = query.data ?? [];
  const now = Date.now();
  return (
    <>
      <StaleBanner failed={query.isError} updatedAt={query.dataUpdatedAt} onRetry={() => query.refetch()} />
      {revoke.isError && <p className="banner warn" role="alert">Could not revoke the token. Check its status below before trying again, because the change may or may not have been applied.</p>}
      {rows.length === 0 ? (
        <EmptyState title="No access tokens" body="Create a token to let an MCP client connect. Tokens are listed here with their status." />
      ) : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Name</th><th>Scopes</th><th>Status</th><th>Created</th><th>Expires</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((t) => <TokenRow key={t.id} t={t} now={now} confirming={confirming === t.id} setConfirming={setConfirming}
                onRevoke={() => revoke.mutate(t.id, { onSuccess: () => setConfirming(null) })} pending={revoke.isPending} />)}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function TokenRow({ t, now, confirming, setConfirming, onRevoke, pending }: {
  t: McpToken; now: number; confirming: boolean; setConfirming: (id: string | null) => void; onRevoke: () => void; pending: boolean;
}) {
  const status = tokenStatus(t, now);
  const label = status === "active" ? "ACTIVE" : status === "expired" ? "EXPIRED" : "REVOKED";
  return (
    <tr>
      <td className="mono small">{t.name}</td>
      <td className="small">{t.scopes.map((s) => <span key={s} className="mono scope-chip">{s}</span>)}</td>
      <td className="small"><span className={`token-status token-${status}`}>{label}</span></td>
      <td className="small dim" title={t.created_at}>{timestamp(t.created_at)} by {t.created_by}</td>
      <td className="small dim" title={t.expires_at}>{timestamp(t.expires_at)}</td>
      <td className="small">
        {status !== "active" ? (
          <span className="dim">{status === "revoked" && t.revoked_at ? `Revoked ${timestamp(t.revoked_at)}` : "Expired"}</span>
        ) : confirming ? (
          <span className="confirm" role="group" aria-label={`Confirm revoking ${t.name}`}>
            <span>Clients using it stop working on their next call. This cannot be undone.</span>{" "}
            <button type="button" className="btn danger" disabled={pending} onClick={onRevoke}>{pending ? "Revoking…" : "Confirm revoke"}</button>{" "}
            <button type="button" className="btn" onClick={() => setConfirming(null)}>Cancel</button>
          </span>
        ) : (
          <button type="button" className="btn" aria-label={`Revoke token ${t.name}`} onClick={() => setConfirming(t.id)}>Revoke</button>
        )}
      </td>
    </tr>
  );
}

function ClientSetup({ endpoint }: { endpoint: string }) {
  return (
    <section className="widget" aria-label="Connect a client">
      <div className="widget-head"><h2>Connect a client</h2></div>
      <div className="widget-body">
        <ol className="plain small">
          <li>Create a token above with only the scopes the client needs.</li>
          <li>Give the client the endpoint and the header below. Replace the placeholders; never paste a token into a shared file.</li>
          <li>Verify the connection. The tool list must show exactly <span className="mono">get_host_health</span>, <span className="mono">get_host_metrics</span> and <span className="mono">list_hosts</span>.</li>
        </ol>
        <p className="small"><b>Endpoint:</b> <span className="mono">{endpoint}</span> (note the trailing slash)</p>
        <p className="small"><b>Header:</b> <span className="mono">Authorization: Bearer &lt;token&gt;</span></p>

        <h3 className="small">Configuration shape</h3>
        <p className="dim small">
          This shape is for clients that accept a static URL and headers in a JSON file. It has not been checked against any specific client. Use your client's documentation for the exact file and key names.
        </p>
        <CopyBlock text={CLIENT_CONFIG_SNIPPET} label="configuration" />

        <h3 className="small">Verify with the official MCP Python SDK</h3>
        <p className="dim small">
          This is the client the server is tested with. Install the <span className="mono">mcp</span> package, replace the placeholders, and run it. A successful run prints the three tool names.
        </p>
        <CopyBlock text={PYTHON_SNIPPET.replace("<endpoint>", endpoint)} label="verification script" />

        <h3 className="small">If it does not connect</h3>
        <ul className="plain small">
          <li><b>401:</b> the token is unknown, expired, revoked, or the header is missing.</li>
          <li>Keep the trailing slash in the endpoint. The bare path without it was not tested.</li>
          <li>A request rejected before authentication can mean the server does not allow this Host name. The operator sets that allow-list.</li>
          <li><b>rate_limited:</b> more than 60 calls in a minute on this token. Wait a minute.</li>
          <li>Tool errors name the scope or the platform error code. A host outside this workspace and a missing host return the same error.</li>
        </ul>
      </div>
    </section>
  );
}

function CopyBlock({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "copied" | "blocked">("idle");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("blocked");
    }
  };
  return (
    <div className="copy-block">
      <pre className="mono small snippet" aria-label={`${label} example`}>{text}</pre>
      <button type="button" className="btn" onClick={copy}>{state === "copied" ? "Copied" : `Copy ${label}`}</button>
      {state === "blocked" && <span className="dim small"> Copying was blocked. Select the text and copy it manually.</span>}
    </div>
  );
}
