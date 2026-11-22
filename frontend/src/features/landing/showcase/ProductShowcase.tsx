import { useMemo, useState, type ComponentType, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useInRouterContext } from "react-router-dom";
import { ApiProvider } from "../../../api/context";
import { OverviewPage } from "../../overview/OverviewPage";
import { HostsPage } from "../../hosts/HostsPage";
import { MetricsExplorerPage } from "../../explorer/MetricsExplorerPage";
import { SignalGraphPage } from "../../signals/SignalGraphPage";
import { InvestigationsListPage } from "../../investigation/InvestigationsListPage";
import { StatesPage } from "../../states/StatesPage";
import { RehearsalPage } from "../../rehearsal/RehearsalPage";
import { createDemoApi } from "./demoApi";

/** The seven screens shown on the landing page, in the order of the console's sidebar. */
const VIEWS: { id: string; label: string; Page: ComponentType }[] = [
  { id: "command-center", label: "Command Center", Page: OverviewPage },
  { id: "hosts", label: "Hosts", Page: HostsPage },
  { id: "metrics", label: "Metrics Explorer", Page: MetricsExplorerPage },
  { id: "signal-graph", label: "Signal Graph", Page: SignalGraphPage },
  { id: "investigations", label: "Investigations", Page: InvestigationsListPage },
  { id: "states", label: "Operational States", Page: StatesPage },
  { id: "rehearsal", label: "Failure Rehearsal", Page: RehearsalPage },
];

/**
 * Read-only preview of the real console. Each screen is the same component the signed-in app renders, reading the
 * demonstration API through its own query cache, so it never touches the app's session data.
 * The frame is `inert`, so its links and buttons cannot be used from the public page.
 */
/** The app already has a router, so the screens use it. Without one (tests, or a standalone render) the preview brings its own. */
function Routed({ children }: { children: ReactNode }) {
  const inRouter = useInRouterContext();
  return inRouter ? <>{children}</> : <MemoryRouter>{children}</MemoryRouter>;
}

/** Sets the inert property directly, so the frame is non-interactive without relying on how React types the attribute. */
const markInert = (el: HTMLDivElement | null) => {
  if (el) el.inert = true;
};

export function ProductShowcase() {
  const [active, setActive] = useState(0);
  const api = useMemo(() => createDemoApi(), []);
  const queries = useMemo(
    () => new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 60_000 } } }),
    [],
  );
  const view = VIEWS[active] ?? VIEWS[0]!;
  const Page = view.Page;

  return (
    <div className="showcase">
      <div className="showcase-tabs" role="tablist" aria-label="Console screens">
        {VIEWS.map((v, i) => (
          <button
            key={v.id}
            type="button"
            role="tab"
            id={`showcase-tab-${v.id}`}
            aria-selected={i === active}
            aria-controls="showcase-panel"
            tabIndex={i === active ? 0 : -1}
            className={i === active ? "showcase-tab on" : "showcase-tab"}
            onClick={() => setActive(i)}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                e.preventDefault();
                const next = (active + (e.key === "ArrowRight" ? 1 : VIEWS.length - 1)) % VIEWS.length;
                setActive(next);
                const target = VIEWS[next];
                if (target) document.getElementById(`showcase-tab-${target.id}`)?.focus();
              }
            }}
          >
            {v.label}
          </button>
        ))}
      </div>
      <div className="showcase-frame" role="tabpanel" id="showcase-panel" aria-labelledby={`showcase-tab-${view.id}`}>
        <div className="showcase-bar">
          <span className="showcase-dots" aria-hidden="true"><i /><i /><i /></span>
          <span className="showcase-url">uptimetrace / {view.label.toLowerCase()}</span>
          <span className="showcase-tag">Demonstration data</span>
        </div>
        <div className="showcase-screen" ref={markInert} aria-label={`${view.label} preview, read-only`}>
          <QueryClientProvider client={queries}>
            <ApiProvider client={api}>
              <Routed>
                <div className="showcase-page">
                  <Page />
                </div>
              </Routed>
            </ApiProvider>
          </QueryClientProvider>
        </div>
      </div>
      <p className="showcase-note">
        Demonstration data. The hosts, alerts and readings are invented to show the console. They are not customer telemetry.
        Sign in to see your own fleet.
      </p>
    </div>
  );
}
