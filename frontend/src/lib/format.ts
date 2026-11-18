/** Display formatting. A missing value (null/undefined) is always rendered as an em dash, never as 0. */
export const DASH = "—";

export function pct(v: number | null | undefined, digits = 1): string {
  return v == null || !Number.isFinite(v) ? DASH : `${v.toFixed(digits)}%`;
}

export function bytesPerSecond(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return DASH;
  const units = ["B/s", "KB/s", "MB/s", "GB/s"];
  let n = Math.max(0, v);
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
  return `${n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)} ${units[i]}`;
}

export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

export function ago(seconds: number): string {
  return `${duration(seconds)} ago`;
}

export function timestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z").replace(/:\d\dZ$/, "Z");
}
