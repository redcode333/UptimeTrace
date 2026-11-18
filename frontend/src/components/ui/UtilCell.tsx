import { DASH, pct } from "../../lib/format";

/** Compact utilisation readout. null renders as an em dash with no bar: "no data" is not "0%". */
export function UtilCell({ value, warn = 85, crit = 95 }: { value: number | null; warn?: number; crit?: number }) {
  if (value == null) return <span className="dim" title="No fresh measurement">{DASH}</span>;
  const level = value >= crit ? "crit" : value >= warn ? "warn" : "ok";
  return (
    <span className="util">
      <span className="util-bar" aria-hidden="true"><i className={level} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} /></span>
      <span className="mono">{pct(value)}</span>
    </span>
  );
}
