import { useFleetFacets } from "../../api/hooks";
import { SOURCES, SOURCE_LABEL, useScope } from "../../lib/scope";

/**
 * Workspace-wide LIVE / SYNTHETIC / REPLAY filter. Counts are the real number of hosts per source (from the API's
 * facets), shown whether or not the source is selected. At least one source always stays selected.
 */
export function ScopeToggle() {
  const { selected, toggle, canDeselect } = useScope();
  const facets = useFleetFacets();
  const counts = facets.data?.facets.source;
  return (
    <div className="scope" role="group" aria-label="Data source scope">
      {SOURCES.map((s) => {
        const on = selected.includes(s);
        const last = on && !canDeselect;
        return (
          <button
            key={s}
            type="button"
            className={`scope-btn${on ? " on" : ""}`}
            aria-pressed={on}
            aria-disabled={last || undefined}
            title={last ? "At least one source must stay selected." : undefined}
            onClick={() => toggle(s)}
          >
            <i className="ck" aria-hidden="true" />
            {SOURCE_LABEL[s]} <b>{counts ? counts[s] : "–"}</b>
          </button>
        );
      })}
    </div>
  );
}
