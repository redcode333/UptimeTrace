import { ago } from "../../lib/format";

/** Shown when a background refresh failed but earlier data is still on screen. */
export function StaleBanner({ updatedAt, failed, onRetry }: { updatedAt: number; failed: boolean; onRetry: () => void }) {
  if (!failed) return null;
  return (
    <div className="banner warn" role="status">
      <span><b>STALE DATA</b> Refresh failed; showing the last successful response{updatedAt ? ` from ${ago((Date.now() - updatedAt) / 1000)}` : ""}.</span>
      <button type="button" className="btn" onClick={onRetry}>Retry</button>
    </div>
  );
}
