import { RANGES, useTimeRange } from "../../lib/timeRange";

export function TimeRange() {
  const { range, setRange } = useTimeRange();
  return (
    <div className="seg" role="group" aria-label="Time range">
      {RANGES.map((r) => (
        <button key={r.id} type="button" className={r.id === range ? "on" : ""} aria-pressed={r.id === range} onClick={() => setRange(r.id)}>
          {r.label}
        </button>
      ))}
    </div>
  );
}
