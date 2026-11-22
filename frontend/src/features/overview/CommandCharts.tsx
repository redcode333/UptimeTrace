import { useState, type MouseEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { AlertHistogram, Host, Ranking } from "../../api/types";
import { SourceBadge } from "../../components/ui/Badges";
import { ago } from "../../lib/format";
import { hexLayout, type Insight, type Tone } from "./commandCenterModel";

const TONE_FILL: Record<Tone, string> = {
  normal: "#1f8f73", warning: "#e0951f", critical: "#e5483a", offline: "#4b5b6b", neutral: "#2e3a46",
};
const TONE_SIDE: Record<Tone, string> = {
  normal: "#145e4c", warning: "#8a5a12", critical: "#7a2219", offline: "#2e3a46", neutral: "#222b35",
};
const TONE_WORD: Record<Tone, string> = { normal: "healthy", warning: "warning", critical: "critical", offline: "offline", neutral: "stale or unknown" };

/** A data request that has not answered or has failed. Shown instead of a result, never as zero. */
export type DataState = "pending" | "failed" | null;

function StateNote({ state, what }: { state: Exclude<DataState, null>; what: string }) {
  const cap = `${what[0]!.toUpperCase()}${what.slice(1)}`;
  return <p className="dim small">{state === "pending" ? `Loading ${what}…` : `${cap} unavailable: the request failed. It retries every 15 seconds.`}</p>;
}

/** Why a host has no current value. A host that is not reporting is stale or silent; otherwise no sample has arrived. */
export function missingReason(h: Host): string {
  return h.reporting_status === "stale" || h.reporting_status === "silent" ? "stale or silent: no fresh value" : "no sample reported yet";
}

/** Hosts drawn on the map at once. The rest of the in-scope fleet is one click away in the Hosts list. */
export const MAP_PAGE = 60;

/**
 * Host map. Colour is the reported health state; a white ring marks LIVE, a dotted ring REPLAY, hatching SYNTHETIC.
 * In 3D each column rises with the CPU reported now (the value is shown only when the host is reporting); otherwise it stays flat.
 * Each host links to its detail page. The caption states how many in-scope hosts this page represents.
 */
export function HostHexMap({ hosts, total, carry, threeD, state }: {
  hosts: Host[]; total: number | undefined; carry: string; threeD: boolean; state: DataState;
}) {
  const navigate = useNavigate();
  const [page, setPage] = useState(0);
  if (state) return <StateNote state={state} what="host map" />;
  const pages = Math.max(1, Math.ceil(hosts.length / MAP_PAGE));
  const current = Math.min(page, pages - 1);
  const shown = hosts.slice(current * MAP_PAGE, current * MAP_PAGE + MAP_PAGE);
  const cols = Math.max(1, Math.min(8, Math.ceil(Math.sqrt(shown.length || 1))));
  const cells = hexLayout(shown, cols);
  const rows = Math.ceil(shown.length / cols) || 1;
  const width = Math.sqrt(3) * 19 * cols + 28;
  const height = 60 + rows * 19 * 1.5 + (threeD ? 60 : 0);
  const pattern = "cc-hatch";
  const inScope = total ?? hosts.length;
  const first = current * MAP_PAGE + 1;
  const last = current * MAP_PAGE + shown.length;
  const unloaded = Math.max(0, inScope - hosts.length);
  return (
    <figure className="cc-map">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Host map: hosts ${first}–${last} of ${inScope} in scope`} className="cc-map-svg">
        <defs>
          <pattern id={pattern} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="1.6" height="5" fill="#0a1420" fillOpacity=".45" />
          </pattern>
        </defs>
        {cells.map((c) => {
          const base = hexVerts(c.x, c.y);
          const cpu = c.cpu;
          const lift = threeD && cpu !== null ? 3 + Math.max(0, Math.min(100, cpu)) * 0.24 : 0;
          const top = base.map(([x, y]) => [x, y - lift] as const);
          const topPts = top.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
          const isLive = c.host.source === "live";
          const isReplay = c.host.source === "replay";
          const isSynthetic = c.host.source === "synthetic";
          const cpuText = cpu !== null ? `CPU ${Math.round(cpu)}%` : `no current CPU: ${missingReason(c.host)}`;
          const label = `${c.host.hostname} · ${c.host.source.toUpperCase()} · ${TONE_WORD[c.tone]} · ${cpuText}`;
          const href = `/hosts/${encodeURIComponent(c.host.host_id)}${carry}`;
          const open = (e: MouseEvent) => {
            e.preventDefault();
            navigate(href);
          };
          return (
            <a key={c.host.host_id} href={href} onClick={open} className="cc-cell" aria-label={label}>
              {lift > 0 && base.map(([x0, y0], i) => {
                const [x1, y1] = base[(i + 1) % 6]!;
                const [tx0, ty0] = top[i]!;
                const [tx1, ty1] = top[(i + 1) % 6]!;
                const quad = `${tx0.toFixed(1)},${ty0.toFixed(1)} ${tx1.toFixed(1)},${ty1.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)} ${x0.toFixed(1)},${y0.toFixed(1)}`;
                return <polygon key={i} points={quad} fill={TONE_SIDE[c.tone]} stroke="#0a1420" strokeWidth=".5" />;
              })}
              <polygon
                points={topPts}
                fill={TONE_FILL[c.tone]}
                stroke={isLive ? "#ffffff" : isReplay ? "#a3b8c8" : "#0f1d2c"}
                strokeWidth={isLive ? 2 : isReplay ? 1.5 : 1}
                strokeDasharray={isReplay ? "2 2" : undefined}
              >
                <title>{label}</title>
              </polygon>
              {isSynthetic && <polygon points={topPts} fill={`url(#${pattern})`} stroke="none" />}
            </a>
          );
        })}
      </svg>
      <figcaption className="cc-map-legend">
        <span><i className="cc-key" style={{ background: TONE_FILL.normal }} />healthy</span>
        <span><i className="cc-key" style={{ background: TONE_FILL.warning }} />warning</span>
        <span><i className="cc-key" style={{ background: TONE_FILL.critical }} />critical</span>
        <span><i className="cc-key" style={{ background: TONE_FILL.offline }} />offline</span>
        <span>hatched = synthetic · white ring = live · dotted ring = replay{threeD ? " · height = CPU now" : ""}</span>
      </figcaption>
      <p className="dim small cc-foot">
        Showing hosts {first}–{last} of {inScope} in scope{unloaded > 0 ? `; ${unloaded} more are not loaded here` : ""}.{" "}
        {pages > 1 && (
          <>
            <button type="button" className="btn" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous {MAP_PAGE}</button>{" "}
            <button type="button" className="btn" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>Next {MAP_PAGE}</button>{" "}
          </>
        )}
        <Link to={`/hosts${carry}`}>Inspect all {inScope} in Hosts</Link>
      </p>
    </figure>
  );
}

/** The six corners of a flat hex centred on (cx, cy), as [x, y] pairs. */
function hexVerts(cx: number, cy: number): Array<[number, number]> {
  const sz = 19;
  const out: Array<[number, number]> = [];
  for (let a = 0; a < 6; a++) {
    const ang = (Math.PI / 180) * (60 * a - 30);
    out.push([cx + sz * 0.94 * Math.cos(ang), cy + sz * 0.94 * Math.sin(ang)]);
  }
  return out;
}

/** Server-ranked hosts for one metric, each with the time its value was observed, and the count of hosts that could not be ranked. */
export function RankedList({ label, data, state, carry }: {
  label: string; data: Ranking | undefined; state: DataState; carry: string;
}) {
  if (state) return <StateNote state={state} what={label.toLowerCase()} />;
  if (!data) return null;
  const now = Date.now();
  if (data.items.length === 0) {
    return (
      <p className="dim small">
        {data.total_in_scope === 0
          ? "No hosts in the selected sources."
          : `No host in scope has a current value, so none is ranked. ${data.total_in_scope} in scope.`}
      </p>
    );
  }
  return (
    <>
      <ul className="cc-bars" aria-label={label}>
        {data.items.map((r) => (
          <li key={r.host_id}>
            <Link to={`/hosts/${encodeURIComponent(r.host_id)}${carry}`} className="cc-bar-name">{r.hostname}</Link>
            <span className="cc-bar-track" aria-hidden="true"><i style={{ width: `${Math.max(0, Math.min(100, r.value))}%` }} /></span>
            <span className="cc-bar-value mono">{Math.round(r.value)}%</span>
            <span className="dim small mono" title={r.observed_at}>{ago(Math.max(0, (now - Date.parse(r.observed_at)) / 1000))}</span>
            <SourceBadge source={r.source} />
          </li>
        ))}
      </ul>
      <p className="dim small cc-foot">
        Top {data.items.length} of {data.with_value} in scope with a current value. {data.without_value} in scope {data.without_value === 1 ? "has" : "have"} no
        current value and {data.without_value === 1 ? "is" : "are"} not ranked (never counted as zero).
      </p>
    </>
  );
}

/**
 * Alerts opened per bucket over the selected window, as counted by the database over every qualifying alert. Empty buckets are
 * drawn at zero. Each bar's tooltip gives the bucket's UTC start and end and its counts.
 */
export function AlertHistogramChart({ data, state, rangeLabel }: {
  data: AlertHistogram | undefined; state: DataState; rangeLabel: string;
}) {
  if (state) return <StateNote state={state} what="alert history" />;
  if (!data) return null;
  const max = Math.max(1, ...data.buckets.map((b) => b.critical + b.warning));
  const bucketMs = data.bucket_seconds * 1000;
  const bucketLabel = data.bucket_seconds >= 3600 ? `${data.bucket_seconds / 3600} h` : `${data.bucket_seconds / 60} min`;
  return (
    <figure className="cc-hist">
      {data.total === 0 ? (
        <p className="dim small">No alert opened in the last {rangeLabel}.</p>
      ) : (
        <svg viewBox="0 0 240 120" role="img" aria-label={`${data.total} alerts opened in the last ${rangeLabel}: ${data.critical} critical, ${data.warning} warning`} preserveAspectRatio="none">
          {data.buckets.map((b, i) => {
            const w = 240 / data.buckets.length;
            const hc = (b.critical / max) * 110;
            const hw = (b.warning / max) * 110;
            const from = b.start.slice(11, 16);
            const to = new Date(Date.parse(b.start) + bucketMs).toISOString().slice(11, 16);
            return (
              <g key={b.start}>
                <title>{`${from}–${to} UTC: ${b.critical} critical, ${b.warning} warning`}</title>
                {hc > 0 && <rect x={i * w + 1} y={120 - hc} width={w - 2} height={hc} fill={TONE_FILL.critical} />}
                {hw > 0 && <rect x={i * w + 1} y={120 - hc - hw} width={w - 2} height={hw} fill={TONE_FILL.warning} />}
              </g>
            );
          })}
        </svg>
      )}
      <figcaption className="cc-map-legend">
        <span><i className="cc-key" style={{ background: TONE_FILL.critical }} />critical</span>
        <span><i className="cc-key" style={{ background: TONE_FILL.warning }} />warning</span>
        <span>{data.total} opened · {rangeLabel} · per {bucketLabel} · all sources in scope</span>
      </figcaption>
    </figure>
  );
}

export function InsightList({ insights }: { insights: Insight[] }) {
  return (
    <ul className="cc-insights" aria-label="Insights">
      {insights.map((i) => (
        <li key={i.id} className={`cc-insight cc-insight-${i.tone}`}>
          <h3>{i.title}</h3>
          <p>{i.body}</p>
          <span className="dim mono small">{i.meta}</span>
        </li>
      ))}
    </ul>
  );
}
