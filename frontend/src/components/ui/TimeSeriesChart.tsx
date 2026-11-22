import { useMemo, useRef, useState } from "react";
import type { Series } from "../../api/types";
import { DASH, bytesPerSecond, pct } from "../../lib/format";

const COLORS = ["var(--s1)", "var(--s2)", "var(--s4)", "var(--s3)"];
const W = 720;
const PAD = { l: 52, r: 12, t: 10, b: 22 };

export type Unit = "percent" | "bytes_per_second";
const fmt = (u: Unit, v: number | null) => (u === "percent" ? pct(v) : bytesPerSecond(v));

export function seriesName(s: Series): string {
  const parts = Object.values(s.labels);
  return parts.length ? parts.join(" · ") : "value";
}

function niceMax(max: number): number {
  if (max <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(max));
  return Math.ceil(max / p) * p;
}

/**
 * Dependency-free SVG line chart. Null points are gaps (the line breaks), never zero. Hover shows a tooltip with the
 * value of every series at the nearest time bucket. Percent charts are fixed 0-100 so levels stay comparable.
 */
/** `threshold` draws a dashed line at a configured limit (percent charts only); it is drawn only when given. */
export function TimeSeriesChart({ series, unit, start, end, label, height = 220, threshold }: { series: Series[]; unit: Unit; start: string; end: string; label: string; height?: number; threshold?: { value: number; label: string } }) {
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<SVGSVGElement>(null);
  const t0 = Date.parse(start);
  const t1 = Date.parse(end);
  const { ymax, hasData } = useMemo(() => {
    const all = series.flatMap((s) => s.points.map((p) => p.value).filter((v): v is number => v != null));
    return { ymax: unit === "percent" ? 100 : niceMax(all.length ? Math.max(...all) : 0), hasData: all.length > 0 };
  }, [series, unit]);
  const x = (t: number) => PAD.l + ((t - t0) / (t1 - t0 || 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - v / ymax) * (height - PAD.t - PAD.b);

  const grid = [0, 0.25, 0.5, 0.75, 1].map((f) => f * ymax);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => t0 + f * (t1 - t0));
  const timeFmt = (t: number) => {
    const iso = new Date(t).toISOString();
    return t1 - t0 > 36 * 3_600_000 ? `${iso.slice(5, 10)} ${iso.slice(11, 16)}` : iso.slice(11, 16);
  };

  const times = series[0]?.points.map((p) => Date.parse(p.t)) ?? [];
  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    const r = ref.current?.getBoundingClientRect();
    if (!r || !times.length) return;
    const px = ((e.clientX - r.left) / r.width) * W;
    let best = 0;
    times.forEach((t, i) => { if (Math.abs(x(t) - px) < Math.abs(x(times[best]!) - px)) best = i; });
    setHover(best);
  }

  const paths = series.map((s) => {
    let d = "";
    let pen = false;
    s.points.forEach((p) => {
      if (p.value == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(Date.parse(p.t)).toFixed(1)},${y(p.value).toFixed(1)}`;
      pen = true;
    });
    // an isolated point has no line segment: draw it as a dot so it is not lost
    const dots = s.points.filter((p, i) => p.value != null && s.points[i - 1]?.value == null && s.points[i + 1]?.value == null);
    return { d, dots };
  });

  const hp = hover != null ? times[hover] : undefined;
  return (
    <div className="chart">
      <svg ref={ref} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={label} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {grid.map((g) => (
          <g key={g}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(g)} y2={y(g)} className="grid" />
            <text x={PAD.l - 6} y={y(g) + 3} textAnchor="end" className="axis">{unit === "percent" ? `${g}%` : bytesPerSecond(g)}</text>
          </g>
        ))}
        {ticks.map((t) => <text key={t} x={x(t)} y={height - 6} textAnchor="middle" className="axis">{timeFmt(t)}</text>)}
        {paths.map((p, i) => (
          <g key={i} stroke={COLORS[i % COLORS.length]} fill={COLORS[i % COLORS.length]}>
            <path d={p.d} fill="none" strokeWidth={1.6} strokeLinejoin="round" />
            {p.dots.map((pt) => <circle key={pt.t} cx={x(Date.parse(pt.t))} cy={y(pt.value!)} r={2} stroke="none" />)}
          </g>
        ))}
        {threshold && unit === "percent" && (
          <g>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(threshold.value)} y2={y(threshold.value)} stroke="#e5483a" strokeWidth={1} strokeDasharray="3 3" />
            <text x={W - PAD.r} y={y(threshold.value) - 3} textAnchor="end" className="axis">{threshold.label}</text>
          </g>
        )}
        {hp != null && <line x1={x(hp)} x2={x(hp)} y1={PAD.t} y2={height - PAD.b} className="cursor" />}
      </svg>
      {hp != null && hover != null && (
        <div className="tip" role="tooltip" style={{ left: `${Math.min(80, (x(hp) / W) * 100)}%` }}>
          <b className="mono">{new Date(hp).toISOString().replace("T", " ").slice(0, 16)}Z</b>
          {series.map((s, i) => (
            <div key={i}><i style={{ background: COLORS[i % COLORS.length] }} aria-hidden="true" />{seriesName(s)}: <span className="mono">{s.points[hover]?.value == null ? `${DASH} no data` : fmt(unit, s.points[hover]!.value)}</span></div>
          ))}
        </div>
      )}
      {series.length > 1 && (
        <ul className="legend">{series.map((s, i) => <li key={i}><i style={{ background: COLORS[i % COLORS.length] }} aria-hidden="true" />{seriesName(s)}</li>)}</ul>
      )}
      {!hasData && <p className="dim chart-empty">No samples in this window.</p>}
    </div>
  );
}
