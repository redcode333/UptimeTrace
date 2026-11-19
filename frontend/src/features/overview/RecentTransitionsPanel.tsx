import { Link } from "react-router-dom";
import type { UseQueryResult } from "@tanstack/react-query";
import type { AlertList, Host, Source } from "../../api/types";
import { ErrorState, Loading } from "../../components/states/States";
import { PartialBanner, StaleBanner } from "../../components/ui/Banners";
import { HealthBadge } from "../../components/ui/Badges";
import { ago } from "../../lib/format";
import { summarizeAlerts } from "../../lib/commandCenter";
import { TRANSITION_LABEL, recentTransitions, transitionsComplete } from "../../lib/alertTransitions";
import type { Transition } from "../../lib/alertTransitions";

export interface RecentTransitionsPanelProps {
  query: UseQueryResult<AlertList>;
  hostIndex: Map<string, Host> | null;
  hostsComplete: boolean;
  selected: Source[];
  carry: string;
  now: number;
}

const LIMIT = 200;

/** CC-6: the four documented alert transitions, newest first, for the selected scope (docs/FRONTEND_ARCHITECTURE.md section 6.10). */
export function RecentTransitionsPanel({ query, hostIndex, hostsComplete, selected, carry, now }: RecentTransitionsPanelProps) {
  return (
    <section className="widget" aria-label="Recent alert transitions">
      <div className="widget-head"><h2>Recent alert transitions</h2><span className="dim">last 24 hours</span></div>
      <div className="widget-body">
        {query.isPending && <Loading label="Loading alert transitions" />}
        {query.isError && !query.data && <ErrorState error={query.error} onRetry={() => query.refetch()} />}
        {query.data && (
          <Body data={query.data} isStale={query.isError} onRetry={() => query.refetch()} updatedAt={query.dataUpdatedAt}
            hostIndex={hostIndex} hostsComplete={hostsComplete} selected={selected} carry={carry} now={now} />
        )}
        <p className="dim small caption">
          Alert transitions only. Severity changes, agent starts and stops, and host state changes are not recorded here.
        </p>
      </div>
    </section>
  );
}

function Body(p: {
  data: AlertList; isStale: boolean; onRetry: () => void; updatedAt: number;
  hostIndex: Map<string, Host> | null; hostsComplete: boolean; selected: Source[]; carry: string; now: number;
}) {
  const s = summarizeAlerts(p.data.alerts, p.data.total, p.selected, p.hostIndex, p.hostsComplete);
  // Transitions are only claimed for alerts whose source is known: unresolved alerts are left out and that is said.
  const inScope = s.inScope;
  const transitions = recentTransitions(inScope, new Date(p.now), undefined, 20);
  const listComplete = transitionsComplete(p.data.alerts.length, p.data.total);
  const joinComplete = !s.joinNeeded || (p.hostsComplete && s.notLoaded === 0);
  const claimEmpty = listComplete && joinComplete;

  return (
    <>
      <StaleBanner failed={p.isStale} updatedAt={p.updatedAt} onRetry={p.onRetry} />
      {!listComplete && (
        <PartialBanner>Covers the {LIMIT} newest alerts. Transitions of older alerts are not shown.</PartialBanner>
      )}
      {s.joinNeeded && s.notLoaded > 0 && (
        <p className="dim small">{s.notLoaded} alert{s.notLoaded === 1 ? "" : "s"} from hosts not in the loaded host list {s.notLoaded === 1 ? "is" : "are"} left out because their source is unknown.</p>
      )}
      {transitions.length === 0 ? (
        claimEmpty
          ? <p className="dim">No alert transitions in the last 24 hours.</p>
          : <p className="dim">No transitions among the alerts loaded. Older alerts may still have transitions in the window and are not shown.</p>
      ) : (
        <ol className="transitions" aria-label="Alert transitions, newest first">
          {transitions.map((t) => <Row key={t.id} t={t} now={p.now} carry={p.carry} />)}
        </ol>
      )}
    </>
  );
}

function Row({ t, now, carry }: { t: Transition; now: number; carry: string }) {
  const seconds = Math.max(0, (now - Date.parse(t.at)) / 1000);
  return (
    <li className="transition">
      <span className="mono small dim" title={t.at}>{ago(seconds)}</span>{" "}
      <b>{TRANSITION_LABEL[t.kind]}</b>
      {t.actor && <span className="dim small"> by {t.actor}</span>}{" "}
      <HealthBadge status={t.severity} />{" "}
      <Link className="hostlink" to={`/hosts/${encodeURIComponent(t.hostId)}${carry}`}>{t.hostId}</Link>{" · "}
      <Link to={`/alerts/${encodeURIComponent(t.alertId)}${carry}`}>Open alert</Link>
    </li>
  );
}
