import { useLocation } from "react-router-dom";
import { ScopeToggle } from "./ScopeToggle";
import { TimeRange } from "./TimeRange";
import { UserMenu } from "./UserMenu";
import { breadcrumbs } from "./nav";

export function TopBar({ onOpenMenu, menuOpen }: { onOpenMenu: () => void; menuOpen: boolean }) {
  const { pathname } = useLocation();
  const crumbs = breadcrumbs(pathname);
  return (
    <header className="topbar">
      <button type="button" className="burger" aria-label="Open menu" aria-controls="app-sidebar" aria-expanded={menuOpen} onClick={onOpenMenu}>
        <svg viewBox="0 0 24 24" aria-hidden="true" className="icon"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
      </button>
      <nav aria-label="Breadcrumb" className="crumb">
        {crumbs.map((c, i) => (i === crumbs.length - 1 ? <b key={i} aria-current="page">{c}</b> : <span key={i}>{c} / </span>))}
      </nav>
      {/* Not functional yet: there is no search backend. Shown disabled so the layout is final, never pretending to work. */}
      <button type="button" className="search" disabled aria-disabled="true" title="Search is not available yet">
        Search (not available yet)
      </button>
      <ScopeToggle />
      <div className="topbar-right">
        <TimeRange />
        <button type="button" className="chip" disabled aria-disabled="true" title="Notifications are not available yet">
          Notifications
        </button>
        <UserMenu />
      </div>
    </header>
  );
}
