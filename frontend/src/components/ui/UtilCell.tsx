import { DASH, pct } from "../../lib/format";

/** Compact utilisation readout. null renders as an em dash with no bar: "no data" is not "0%". */
/**
 * `neutral` draws the value without a state colour. Used where no configured limit exists, so nothing is judged.
 * Callers that know the configured limits pass them in `warn` and `crit`.
 */
export function UtilCell({ value, warn = 85, crit = 95, neutral = false }: { value: number | null; warn?: number; crit?: number; neutral?: boolean }) {
  if (value == null) return <span className="dim" title="No fresh measurement">{DASH}</span>;
  const level = neutral ? "" : value >= crit ? "crit" : value >= warn ? "warn" : "ok";
  return (
    <span className="util">
      <span className="util-bar" aria-hidden="true"><i className={level} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} /></span>
      <span className="mono">{pct(value)}</span>
    </span>
  );
}
