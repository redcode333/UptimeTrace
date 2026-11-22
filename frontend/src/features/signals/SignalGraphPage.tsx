import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQueries } from "@tanstack/react-query";
import { useApi } from "../../api/context";
import { useHostList } from "../../api/hooks";
import type { LatestMetrics } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { ago, timestamp } from "../../lib/format";
import { scopeFilter, useScope } from "../../lib/scope";
import { NOT_COLLECTED, buildSignalModel, type SignalComponent, type SignalModel, type ThreadStatus } from "./signalModel";
import "./signalGraph.css";

const STATUS_LABEL: Record<ThreadStatus, string> = {
  flowing: "flowing",
  stale: "stale",
  silent: "silent",
  "no-sample": "no report yet",
};

const STATUS_CLASS: Record<ThreadStatus, string> = {
  flowing: "sg-st-ok",
  stale: "sg-st-stale",
  silent: "sg-st-off",
  "no-sample": "sg-st-none",
};

const STATUS_MEANING: Record<ThreadStatus, string> = {
  flowing: "The latest sample is within the freshness window.",
  stale: "The latest sample is older than the freshness window, or was flagged stale. Its value is the last one received, not a current reading.",
  silent: "The host has stopped reporting, so this value is the last one received.",
  "no-sample": "No sample has arrived for this component yet.",
};

/** The host list page size the server allows. Hosts beyond it are not drawn, and the page says how many. */
const HOST_PAGE = 200;

export function SignalGraphPage() {
  const api = useApi();
  const { selected } = useScope();
  const hosts = useHostList({ page_size: HOST_PAGE, source: scopeFilter(selected) }, { refetchInterval: 15_000 });
  const items = hosts.data?.items ?? [];
  const latestQueries = useQueries({
    queries: items.map((h) => ({
      queryKey: ["latest", h.host_id],
      queryFn: ({ signal }: { signal: AbortSignal }) => api.latest(h.host_id, signal),
      retry: false,
      refetchInterval: 15_000,
    })),
  });
  // Loading and failed reads are both "not known yet" for a host; neither is shown as an empty component list.
  const latest: Record<string, LatestMetrics | null | undefined> = {};
  items.forEach((h, i) => {
    const q = latestQueries[i];
    latest[h.host_id] = q?.isError ? null : q?.data;
  });
  const now = Date.now();
  const model = useMemo(
    () => (hosts.data ? buildSignalModel(items, latest, now, hosts.data.thresholds.stale_after_seconds, hosts.data.facets.source) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [hosts.data, latestQueries.map((q) => q.dataUpdatedAt).join(",")],
  );

  if (hosts.isPending) return <Page><Loading label="Reading what agents report" /></Page>;
  if (hosts.isError && !hosts.data) return <Page><ErrorState error={hosts.error} onRetry={() => hosts.refetch()} /></Page>;
  if (!model || !hosts.data) return null;

  const inScope = hosts.data.total;
  const partial = inScope > items.length;

  return (
    <Page>
      <Scope model={model} />
      {partial && (
        <p className="banner notice" role="status">
          Showing the first {items.length} of {inScope} hosts in scope. Components of the other {inScope - items.length} hosts are not drawn here. Narrow the source scope in the top bar to see them.
        </p>
      )}
      {hosts.isError && <p className="banner warn" role="alert">The host list could not be refreshed. The graph shows the last list it received.</p>}
      {model.lanes.length === 0 ? (
        <EmptyState
          title="No hosts to draw yet"
          body="The graph draws what registered hosts report. Register a host and start its agent; its components appear here after the first sample."
        />
      ) : (
        <>
          <Stage model={model} />
          <ThreadTable model={model} />
          <div className="sg-two">
            <Discovery model={model} />
            <CanSee model={model} />
          </div>
        </>
      )}
    </Page>
  );
}

function Page({ children }: { children: ReactNode }) {
  return (
    <section className="sg" aria-label="Signal Graph">
      <PageHeader
        title="Signal Graph"
        subtitle="The telemetry agents report, one lane per host. Nothing is inferred: no service dependencies, traces or forecasts."
      />
      {children}
    </section>
  );
}

function Scope({ model }: { model: SignalModel }) {
  return (
    <div className="sg-scope">
      <span className="sg-chip" aria-label="Hosts by source">
        Live <b>{model.counts.live}</b>
      </span>
      <span className="sg-chip" aria-label="Synthetic hosts">
        Synthetic <b>{model.counts.synthetic}</b>
      </span>
      <span className="sg-chip" aria-label="Replay hosts">
        Replay <b>{model.counts.replay}</b>
      </span>
      <span className="sg-time" role="group" aria-label="Time window">
        <span aria-current="true" className="sg-now">Latest reading</span>
        <button type="button" disabled title="History is not part of this graph. Each block shows the latest reading; a host's Metrics view has its history.">1h</button>
        <button type="button" disabled title="History is not part of this graph. Each block shows the latest reading; a host's Metrics view has its history.">6h</button>
      </span>
    </div>
  );
}

function Stage({ model }: { model: SignalModel }) {
  const [threeD, setThreeD] = useState(true);
  const [focus, setFocus] = useState<string | null>(null);
  const selected = model.lanes.flatMap((l) => l.components).find((c) => c.id === focus) ?? null;
  const columns = Math.max(1, ...model.lanes.map((l) => l.components.length));
  return (
    <div className="sg-panel">
      <div className="sg-head">
        <h2>Telemetry threads</h2>
        <div className="sg-seg" role="group" aria-label="View">
          <button type="button" aria-pressed={threeD} onClick={() => setThreeD(true)}>3D</button>
          <button type="button" aria-pressed={!threeD} onClick={() => setThreeD(false)}>Flat</button>
        </div>
      </div>
      <div className={threeD ? "sg-stage" : "sg-stage sg-flat"}>
        <div className="sg-plane" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
          {model.lanes.map((lane) => (
            <div className="sg-lane" key={lane.hostId}>
              <div className="sg-lane-name">
                <Link to={`/hosts/${encodeURIComponent(lane.hostId)}`}><b>{lane.hostname}</b></Link>
                <span>{lane.latestAvailable ? lane.reportingStatus : "unknown"}</span>
              </div>
              {lane.components.length === 0 ? (
                <div className="sg-empty-lane">{lane.latestAvailable ? "No component has reported yet" : "Latest read unavailable"}</div>
              ) : (
                lane.components.map((c) => (
                  <button
                    type="button"
                    key={c.id}
                    className={`sg-block ${STATUS_CLASS[c.status]}${focus === c.id ? " sg-sel" : ""}`}
                    style={{ height: blockHeight(c), transform: threeD ? `translateZ(${blockHeight(c)}px)` : undefined }}
                    aria-pressed={focus === c.id}
                    aria-label={`${lane.hostname} ${c.label}: ${STATUS_LABEL[c.status]}${c.utilisation !== null ? `, last reading ${Math.round(c.utilisation)} percent` : ""}`}
                    onClick={() => setFocus(focus === c.id ? null : c.id)}
                  >
                    <span className="sg-block-label">{c.label}</span>
                    {c.utilisation !== null && (
                      <span className="sg-block-value">
                        {Math.round(c.utilisation)}%{c.status !== "flowing" && c.status !== "no-sample" ? " last" : ""}
                      </span>
                    )}
                  </button>
                ))
              )}
            </div>
          ))}
        </div>
      </div>
      <div className="sg-legend">
        <span><i className="sg-key sg-st-ok" />flowing</span>
        <span><i className="sg-key sg-st-stale" />stale</span>
        <span><i className="sg-key sg-st-off" />silent</span>
        <span><i className="sg-key sg-st-none" />no report yet</span>
        <span>Block height = last reported utilisation (utilisation components only). Counters have no height.</span>
      </div>
      <Inspector component={selected} hostname={selected ? model.lanes.find((l) => l.hostId === selected.hostId)?.hostname ?? "" : ""} onClear={() => setFocus(null)} />
    </div>
  );
}

/** The selected thread's real observation, and links to the host and to its metric's history. */
function Inspector({ component, hostname, onClear }: { component: SignalComponent | null; hostname: string; onClear: () => void }) {
  if (!component) {
    return <div className="sg-pad sg-inspect-empty">Select a block to inspect its latest sample. Nothing is selected by default.</div>;
  }
  const hostPath = `/hosts/${encodeURIComponent(component.hostId)}`;
  const metricPath = `/metrics?host=${encodeURIComponent(component.hostId)}&metric=${encodeURIComponent(component.metric)}`;
  return (
    <div className="sg-inspect" aria-label="Selected thread">
      <div className="sg-head">
        <h2>{hostname} · {component.label}</h2>
        <button type="button" className="sg-clear" onClick={onClear}>Clear selection</button>
      </div>
      <dl className="sg-kv">
        <dt>Carries</dt><dd>{component.carries}</dd>
        <dt>Status</dt><dd><span className={`sg-status ${STATUS_CLASS[component.status]}`}>{STATUS_LABEL[component.status]}</span> <span className="dim small">{STATUS_MEANING[component.status]}</span></dd>
        <dt>Last sample</dt>
        <dd className="sg-mono">
          {component.observedAt ? `${timestamp(component.observedAt)} (${component.ageSeconds === null ? "" : ago(component.ageSeconds)})` : "none received"}
        </dd>
        <dt>{component.utilisation !== null ? "Last reading" : "Rate"}</dt>
        <dd className="sg-mono">
          {component.utilisation !== null
            ? `${Math.round(component.utilisation)}%`
            : component.ratePerSecond !== null
              ? `${component.ratePerSecond.toFixed(0)} bytes/s`
              : "not available: a rate needs two consecutive valid samples"}
        </dd>
      </dl>
      <p className="small">
        <Link to={hostPath}>Open host</Link>
        {" · "}
        <Link to={metricPath}>Open this metric's history in Metrics Explorer</Link>
      </p>
    </div>
  );
}

/** Height from the reported utilisation; counters and unknown values get a fixed low block, never a made-up height. */
function blockHeight(c: SignalComponent): number {
  if (c.utilisation === null) return 14;
  return 14 + Math.round(c.utilisation * 1.2);
}

function ThreadTable({ model }: { model: SignalModel }) {
  const rows = model.lanes.flatMap((lane) =>
    lane.components.map((c) => ({ lane, c })),
  );
  return (
    <div className="sg-panel">
      <div className="sg-head">
        <h2>Thread inspector</h2>
        <span>{rows.length} threads</span>
      </div>
      {rows.length === 0 ? (
        <div className="sg-pad">No thread yet. A thread is drawn after a component's first sample arrives.</div>
      ) : (
        <div className="sg-table-wrap">
          <table className="sg-table">
            <thead>
              <tr>
                <th>Host</th>
                <th>Component</th>
                <th>Carries</th>
                <th>Last packet</th>
                <th>Cadence</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ lane, c }) => (
                <tr key={c.id}>
                  <td><Link to={`/hosts/${encodeURIComponent(lane.hostId)}`}>{lane.hostname}</Link></td>
                  <td>{c.label}</td>
                  <td className="sg-mono">{c.carries}</td>
                  <td className="sg-mono">{c.ageSeconds === null ? "—" : ago(c.ageSeconds)}</td>
                  <td className="sg-mono" title="Not measured by this view yet">not measured</td>
                  <td>
                    <span className={`sg-status ${STATUS_CLASS[c.status]}`}>{STATUS_LABEL[c.status]}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Discovery({ model }: { model: SignalModel }) {
  return (
    <div className="sg-panel">
      <div className="sg-head">
        <h2>Reported components</h2>
        <span>{model.totalComponents} total</span>
      </div>
      <ul className="sg-discovery">
        {model.lanes.map((lane) => (
          <li key={lane.hostId}>
            <b>{lane.hostname}</b>
            <span>
              {!lane.latestAvailable
                ? "latest read unavailable"
                : lane.components.length === 0
                  ? "awaiting first sample"
                  : lane.components.map((c) => c.label).join(" · ")}
            </span>
          </li>
        ))}
      </ul>
      <p className="sg-note">A component is listed only after an agent has reported it. Nothing is listed from configuration alone.</p>
    </div>
  );
}

function CanSee({ model }: { model: SignalModel }) {
  return (
    <div className="sg-panel">
      <div className="sg-head">
        <h2>What the graph can see</h2>
        <span>agent telemetry</span>
      </div>
      <ul className="sg-can">
        {model.observedFamilies.map((f) => (
          <li key={f}>
            <span>{f}</span>
            <b className="sg-yes">observed</b>
          </li>
        ))}
        {NOT_COLLECTED.map((f) => (
          <li key={f}>
            <span>{f}</span>
            <b className="sg-no">not collected</b>
          </li>
        ))}
      </ul>
    </div>
  );
}
