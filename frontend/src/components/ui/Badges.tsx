import type { HealthStatus, Source } from "../../api/types";
import { SOURCE_LABEL } from "../../lib/scope";

const SOURCE_TITLE: Record<Source, string> = {
  live: "Connected infrastructure",
  synthetic: "Simulated host, not real infrastructure",
  replay: "Replayed history, not current",
};

/** Source and health are always text plus a shape cue, never colour alone. */
export function SourceBadge({ source }: { source: Source }) {
  return <span className={`src src-${source}`} title={SOURCE_TITLE[source]}>{SOURCE_LABEL[source]}</span>;
}

const HEALTH_LABEL: Record<HealthStatus, string> = { healthy: "HEALTHY", warning: "WARNING", critical: "CRITICAL", stale: "STALE", offline: "OFFLINE", unknown: "UNKNOWN" };
/** Glyphs are part of the status: critical is a solid square and warning a triangle (design decision D-3). */
const HEALTH_SHAPE: Record<HealthStatus, string> = { healthy: "●", warning: "▲", critical: "■", stale: "◐", offline: "○", unknown: "?" };

export function HealthBadge({ status }: { status: HealthStatus }) {
  return <span className={`hb hb-${status}`}><i aria-hidden="true">{HEALTH_SHAPE[status]}</i>{HEALTH_LABEL[status]}</span>;
}

export const HEALTH_ORDER: HealthStatus[] = ["critical", "warning", "stale", "offline", "unknown", "healthy"];
export const healthLabel = (h: HealthStatus) => HEALTH_LABEL[h];
