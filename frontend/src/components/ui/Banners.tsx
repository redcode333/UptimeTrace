import type { ReactNode } from "react";
import { ago } from "../../lib/format";

/** Shown when a background refresh failed but earlier data is still on screen. */
export function StaleBanner({ updatedAt, failed, onRetry }: { updatedAt: number; failed: boolean; onRetry: () => void }) {
  if (!failed) return null;
  return (
    <div className="banner notice" role="status">
      <span><b>STALE DATA</b> Refresh failed; showing the last successful response{updatedAt ? ` from ${ago((Date.now() - updatedAt) / 1000)}` : ""}.</span>
      <button type="button" className="btn" onClick={onRetry}>Retry</button>
    </div>
  );
}

/** Shown when a list or join is capped: the numbers below cover only the rows loaded, and say so. Informational, not a health state. */
export function PartialBanner({ children }: { children: ReactNode }) {
  return (
    <div className="banner notice" role="status" data-testid="partial-coverage">
      <span><b>PARTIAL</b> {children}</span>
    </div>
  );
}
