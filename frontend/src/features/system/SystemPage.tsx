import { Link } from "react-router-dom";
import type { UseQueryResult } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { useHostList, useMcpTokens, useMonitoringSettings, usePlatformHealth, useRegistryHosts } from "../../api/hooks";
import type { PlatformHealth, RegistryHost, Source, Thresholds } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { StaleBanner } from "../../components/ui/Banners";
import { HealthBadge, SourceBadge } from "../../components/ui/Badges";
import { activeCount, MAX_ACTIVE_TOKENS } from "../../lib/mcpTokens";
import { ago, timestamp } from "../../lib/format";
import { HEALTHS } from "../../lib/hostsQuery";
import { SOURCE_LABEL, SOURCES, scopeFilter, useScope } from "../../lib/scope";
import { HEALTH_MEANING } from "../states/StatesPage";

const REGISTRY_STATE_LABEL: Record<RegistryHost["telemetry_state"], string> = {
  awaiting_first_telemetry: "Registered, never reported",
  reporting: "Reporting",
  stale: "Stale",
  silent: "Silent",
  replay: "Replay",
};

/** The whole System board: what the server reports, what it does not, and where the rest is configured. */
export function SystemPage() {
  const { selected } = useScope();
  const source = scopeFilter(selected);
  const health = usePlatformHealth();
  const counts = useHostList({ source, page_size: 1 }, { refetchInterval: 30_000 });
  const newest = useHostList({ source, page_size: 1, sort: "last_seen", order: "desc" }, { refetchInterval: 30_000 });
  const registry = useRegistryHosts({ refetchInterval: 30_000 });
  const settings = useMonitoringSettings();
  const tokens = useMcpTokens();

  return (
    <section className="sg" aria-label="System">
      <PageHeader
        title="System"
        subtitle="What the server reports about the platform, its agents and its configuration. Anything the server does not report is labelled as not reported."
      />
      <PlatformSection q={health} />
      <AgentsSection counts={counts} newest={newest} registry={registry} source={source} selected={selected} />
      <ConfigSection settings={settings} tokens={tokens} />
    </section>
  );
}

function PlatformSection({ q }: { q: UseQueryResult<PlatformHealth> }) {
  return (
    <section className="widget" aria-labelledby="sys-platform">
      <div className="widget-head">
        <h2 id="sys-platform">Platform status</h2>
        <button type="button" className="btn" onClick={() => q.refetch()} disabled={q.isFetching}>{q.isFetching ? "Checking…" : "Check again"}</button>
      </div>
      <div className="widget-body">
        {q.isPending && <Loading label="Checking the API and its database" />}
        {q.isError && !q.data && (
          <>
            <p className="banner warn" role="alert">
              Unavailable: the health check did not answer{q.error instanceof ApiError ? ` (${q.error.message})` : ""}. This is not a healthy result.
            </p>
          </>
        )}
        {q.data && (
          <>
            <StaleBanner failed={q.isError} updatedAt={q.dataUpdatedAt} onRetry={() => q.refetch()} />
            <p className="small"><b>{overall(q.data)}</b></p>
            <table className="tbl" aria-label="Platform checks">
              <thead><tr><th>Component</th><th>Kind</th><th>Reported</th></tr></thead>
              <tbody>
                <tr>
                  <td>API process</td>
                  <td className="small dim">Liveness: the process answered</td>
                  <td>{q.data.app === "ok" ? "Responding" : `Reported as “${q.data.app}”`}</td>
                </tr>
                <tr>
                  <td>Database</td>
                  <td className="small dim">Readiness: a dependency the API needs</td>
                  <td>{databaseText(q.data.database)}</td>
                </tr>
                <tr>
                  <td>Agents, trace backend, collector</td>
                  <td className="small dim">Not part of this probe</td>
                  <td className="dim">Not reported by the server (see below)</td>
                </tr>
              </tbody>
            </table>
            <p className="dim small">
              HTTP {q.data.http} from GET /health, checked {q.dataUpdatedAt ? timestamp(new Date(q.dataUpdatedAt).toISOString()) : "—"}. Refreshes every 30 seconds.
              A reply from this probe does not prove that agents are sending telemetry or that any other dependency works.
            </p>
          </>
        )}
      </div>
    </section>
  );
}

function overall(d: PlatformHealth): string {
  if (d.status === "ok" && d.database === "ok") return "Both checked components are reachable.";
  if (d.status === "degraded") return "Degraded: a checked dependency is unavailable.";
  return `The server reported status “${d.status}”.`;
}

function databaseText(v: string): string {
  if (v === "ok") return "Reachable";
  if (v === "unavailable") return "Unavailable";
  return `Not reported (“${v}”)`;
}

function AgentsSection({
  counts, newest, registry, source, selected,
}: {
  counts: ReturnType<typeof useHostList>;
  newest: ReturnType<typeof useHostList>;
  registry: ReturnType<typeof useRegistryHosts>;
  source: Source[] | undefined;
  selected: Source[];
}) {
  const facets = counts.data?.facets;
  const top = newest.data?.items[0];
  const registeredInScope = registry.data?.filter((r) => selected.includes(r.kind)) ?? [];
  return (
    <section className="widget" aria-labelledby="sys-agents">
      <div className="widget-head">
        <h2 id="sys-agents">Agents and telemetry</h2>
        <span className="dim small">Scope: {selected.map((s) => SOURCE_LABEL[s]).join(" + ") || "none"} {source ? "" : "(all)"}</span>
      </div>
      <div className="widget-body">
        <p className="small" aria-label="Hosts by source">
          {facets
            ? SOURCES.map((s) => (
              <span key={s} className="sys-src"><SourceBadge source={s} /> <span className="mono">{facets.source[s]}</span> </span>
            ))
            : counts.isPending ? <Loading label="Counting hosts" /> : null}
          <span className="dim small"> · workspace counts; LIVE, SYNTHETIC and REPLAY are never merged.</span>
        </p>
        {counts.isError && !counts.data && <ErrorState error={counts.error} onRetry={() => counts.refetch()} />}
        {facets && (
          <table className="tbl" aria-label="Host health counts">
            <thead><tr><th>Health</th><th className="num">Hosts</th><th>Meaning</th><th><span className="sr-only">Links</span></th></tr></thead>
            <tbody>
              {HEALTHS.map((h) => (
                <tr key={h}>
                  <td><HealthBadge status={h} /></td>
                  <td className="num mono">{facets.health[h]}</td>
                  <td className="small">{HEALTH_MEANING[h]}</td>
                  <td className="small"><Link to={`/hosts?health=${h}`}>Hosts</Link> · <Link to={`/states?health=${h}`}>States</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="small">
          Newest telemetry in scope:{" "}
          {newest.isPending ? <span className="dim">checking…</span>
            : newest.isError && !newest.data ? <span className="dim">unavailable</span>
            : top ? <><Link to={`/hosts/${encodeURIComponent(top.host_id)}`}>{top.hostname}</Link> <span className="mono">{timestamp(top.last_seen)}</span> <span className="dim">({ago(top.last_seen_age_seconds)})</span></>
            : <span className="dim">no host in scope has sent telemetry yet</span>}
        </p>
        <h3 className="small">Registered hosts in scope</h3>
        {registry.isPending && <Loading label="Reading the host registry" />}
        {registry.isError && !registry.data && <ErrorState error={registry.error} onRetry={() => registry.refetch()} />}
        {registry.data && registeredInScope.length === 0 && (
          <EmptyState title="No registered hosts in scope" body="Register a host to start receiving telemetry from its agent." >
            <Link className="btn" to="/onboarding">Register a host</Link>
          </EmptyState>
        )}
        {registry.data && registeredInScope.length > 0 && (
          <table className="tbl" aria-label="Registered host states">
            <thead><tr><th>Telemetry state</th><th className="num">Registered hosts</th></tr></thead>
            <tbody>
              {(Object.keys(REGISTRY_STATE_LABEL) as RegistryHost["telemetry_state"][]).map((st) => {
                const n = registeredInScope.filter((r) => r.telemetry_state === st).length;
                return n > 0 ? (
                  <tr key={st}><td>{REGISTRY_STATE_LABEL[st]}</td><td className="num mono">{n}</td></tr>
                ) : null;
              })}
            </tbody>
          </table>
        )}
        <p className="small">
          <Link to="/hosts">Hosts</Link> · <Link to="/settings/agents">Agents setup</Link> · <Link to="/onboarding">Register a host</Link>
        </p>
      </div>
    </section>
  );
}

function ConfigSection({ settings, tokens }: { settings: ReturnType<typeof useMonitoringSettings>; tokens: ReturnType<typeof useMcpTokens> }) {
  return (
    <section className="widget" aria-labelledby="sys-config">
      <div className="widget-head"><h2 id="sys-config">Configuration and integrations</h2><span className="dim small">Values only; no secrets are shown</span></div>
      <div className="widget-body">
        <h3 className="small">Monitoring thresholds <Badge state={settings.isError ? "unavailable" : settings.data ? "configured" : "unknown"} /></h3>
        {settings.isPending && <Loading label="Reading the configured limits" />}
        {settings.isError && !settings.data && <p className="dim small">The configured limits could not be read.</p>}
        {settings.data && (
          <>
            <ThresholdTable thresholds={settings.data.config} />
            <p className="dim small">
              Version {settings.data.version}
              {settings.data.updated_at ? `, last changed ${timestamp(settings.data.updated_at)}` : ", never changed from the defaults"}
              {" · "}
              {sameAsDefaults(settings.data.config, settings.data.defaults) ? "matches the defaults" : "differs from the defaults"}.
            </p>
          </>
        )}
        <p className="small"><Link to="/settings/alerts">Alert policies</Link> · <Link to="/settings/agents">Agents</Link></p>

        <h3 className="small">Access tokens for MCP <Badge state={tokens.isError ? "unavailable" : tokens.data ? "configured" : "unknown"} /></h3>
        {tokens.isPending && <Loading label="Reading token metadata" />}
        {tokens.isError && !tokens.data && <p className="dim small">Token metadata could not be read.</p>}
        {tokens.data && (
          <p className="small">
            {activeCount(tokens.data, Date.now())} active of {MAX_ACTIVE_TOKENS} allowed. Token values and their hashes are never shown.
            The server does not report whether the MCP endpoint is reachable, so this page does not claim that it is.
            {" "}<Link to="/settings/integrations">Integrations</Link>
          </p>
        )}

        <h3 className="small">Other components</h3>
        <table className="tbl" aria-label="Components the server does not report">
          <thead><tr><th>Component</th><th>Status</th><th>Where to look</th></tr></thead>
          <tbody>
            <tr>
              <td>Trace backend (Jaeger)</td>
              <td><Badge state="unknown" /> <span className="small">Not reported by the server</span></td>
              <td className="small">Each <Link to="/investigations">investigation</Link> states whether traces were available for its window.</td>
            </tr>
            <tr>
              <td>Collector</td>
              <td><Badge state="unknown" /> <span className="small">No collector status exists</span></td>
              <td className="small">Agents send telemetry directly; see Agents and Hosts for their reporting.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ThresholdTable({ thresholds }: { thresholds: { freshness: { stale_after_seconds: number; offline_after_seconds: number }; rules: Thresholds["rules"] } }) {
  return (
    <table className="tbl" aria-label="Configured thresholds">
      <thead><tr><th>Setting</th><th className="num">Value</th></tr></thead>
      <tbody>
        <tr><td>Stale after (no telemetry)</td><td className="num mono">{thresholds.freshness.stale_after_seconds} s</td></tr>
        <tr><td>Offline after (no telemetry)</td><td className="num mono">{thresholds.freshness.offline_after_seconds} s</td></tr>
        {Object.entries(thresholds.rules).map(([metric, r]) => (
          <tr key={metric}>
            <td>{metric.replace("system.", "").replace(".utilization", " utilisation")} · warning / critical</td>
            <td className="num mono">{r.warning}% / {r.critical}%</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function sameAsDefaults(a: { freshness: { stale_after_seconds: number; offline_after_seconds: number }; rules: Record<string, { warning: number; critical: number }> },
  b: typeof a): boolean {
  if (a.freshness.stale_after_seconds !== b.freshness.stale_after_seconds || a.freshness.offline_after_seconds !== b.freshness.offline_after_seconds) return false;
  const keys = new Set([...Object.keys(a.rules), ...Object.keys(b.rules)]);
  return [...keys].every((k) => a.rules[k]?.warning === b.rules[k]?.warning && a.rules[k]?.critical === b.rules[k]?.critical);
}

/** A configuration status, named as the server or the page knows it. "unknown" means no status source exists, never "fine". */
function Badge({ state }: { state: "configured" | "unavailable" | "unknown" }) {
  const label = state === "configured" ? "Configured" : state === "unavailable" ? "Unavailable" : "Unknown";
  return <span className={`sys-badge sys-badge-${state}`}>{label}</span>;
}
