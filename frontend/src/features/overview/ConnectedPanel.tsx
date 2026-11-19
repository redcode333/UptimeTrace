import { Link } from "react-router-dom";
import type { UseQueryResult } from "@tanstack/react-query";
import type { HostList, Source } from "../../api/types";
import { ErrorState, Loading } from "../../components/states/States";
import { StaleBanner } from "../../components/ui/Banners";
import { connectedSummary, coverage } from "../../lib/commandCenter";
import { useScope } from "../../lib/scope";

const NAMES_MAX = 5;

export interface ConnectedPanelProps {
  /** LIVE-only host list. Undefined when LIVE is excluded by the scope (no request is made). */
  query: UseQueryResult<HostList> | undefined;
  selected: Source[];
  carry: string;
}

/** CC-2: LIVE hosts only. Counts come from exact facets; names come from the loaded LIVE rows. Respects the source scope. */
export function ConnectedPanel({ query, selected, carry }: ConnectedPanelProps) {
  const { toggle } = useScope();
  return (
    <section className="connected" aria-label="Connected infrastructure">
      {!selected.includes("live") ? (
        <div className="connected-line">
          <span className="dim">LIVE hidden by scope.</span>{" "}
          <button type="button" className="btn" onClick={() => toggle("live")}>Show LIVE</button>
        </div>
      ) : !query || query.isPending ? (
        <Loading label="Loading connected hosts" />
      ) : query.isError && !query.data ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : query.data ? (
        <Body data={query.data} isStale={query.isError} onRetry={() => query.refetch()} updatedAt={query.dataUpdatedAt} carry={carry} />
      ) : null}
    </section>
  );
}

function Body({ data, isStale, onRetry, updatedAt, carry }: {
  data: HostList; isStale: boolean; onRetry: () => void; updatedAt: number; carry: string;
}) {
  const c = connectedSummary(data);
  const cov = coverage(data.items.length, c.live);
  if (c.live === 0) {
    return (
      <div className="connected-line">
        <span className="dim">No connected hosts have reported. Connected infrastructure appears here after an agent sends its first telemetry.</span>{" "}
        <Link to="/settings/agents">Register a host</Link>
      </div>
    );
  }
  const silentNames = data.items.filter((h) => h.health.status === "offline").slice(0, NAMES_MAX);
  const moreSilent = c.silent - silentNames.length;
  return (
    <>
      <StaleBanner failed={isStale} updatedAt={updatedAt} onRetry={onRetry} />
      <div className="connected-line">
        <b>LIVE</b>{" "}
        <span className="mono">{c.reporting} of {c.live}</span> reporting
        {c.silent > 0 && <> · <span className="mono">{c.silent}</span> silent</>}
        {c.stale > 0 && <> · <span className="mono">{c.stale}</span> stale</>}
        {c.unknown > 0 && <> · <span className="mono">{c.unknown}</span> unknown</>}
        {c.silent > 0 && (
          <div className="small">
            Silent: {silentNames.map((h, i) => (
              <span key={h.host_id}>{i > 0 && ", "}<Link to={`/hosts/${encodeURIComponent(h.host_id)}${carry}`}>{h.hostname}</Link></span>
            ))}
            {moreSilent > 0 && <span className="dim"> and {moreSilent} more</span>}
          </div>
        )}
        {!cov.complete && <div className="dim small">Names shown for the first {cov.loaded} LIVE hosts. Counts cover all {cov.total}.</div>}
      </div>
      <div className="dim small">Source labels are derived from host IDs; stored provenance is not implemented yet.</div>
    </>
  );
}
