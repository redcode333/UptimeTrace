import { NavLink, useSearchParams } from "react-router-dom";
import { globalSearch } from "../../lib/timeRange";
import { NAV } from "./nav";

export function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="icon">
      <path d={d} />
    </svg>
  );
}

interface Props {
  collapsed: boolean;
  open: boolean; // mobile drawer
  onNavigate: () => void;
  onToggleCollapsed: () => void;
}

export function Sidebar({ collapsed, open, onNavigate, onToggleCollapsed }: Props) {
  const [params] = useSearchParams();
  const search = globalSearch(params); // scope and time range follow the user from page to page
  return (
    <nav
      id="app-sidebar"
      aria-label="Main"
      className={`sidebar${collapsed ? " collapsed" : ""}${open ? " open" : ""}`}
    >
      <div className="logo sidebar-logo">
        <i />
        <span className="logo-text">
          UPTIME<b>TRACE</b>
        </span>
      </div>
      {/* Placeholder: workspaces are not implemented, so this does not switch anything. */}
      <button type="button" className="workspace" disabled title="Workspace switching is not available yet">
        <span className="workspace-text">Workspace · single</span>
        <span aria-hidden="true">▾</span>
      </button>
      {NAV.map((group) => (
        <div key={group.label ?? "top"} className="nav-group">
          {group.label && <div className="nav-label">{group.label}</div>}
          {group.items.map((item) => (
            <NavLink
              key={item.path}
              to={{ pathname: item.path, search }}
              end={item.path === "/"}
              title={collapsed ? item.label : undefined}
              aria-label={collapsed ? item.label : undefined}
              onClick={onNavigate}
              className={({ isActive }) => `nav-item${isActive ? " active" : ""}${item.unavailable ? " dim-item" : ""}`}
            >
              <Icon d={item.icon} />
              <span className="nav-text">{item.label}</span>
              {item.tag && <span className="tag nav-tag">{item.tag}</span>}
            </NavLink>
          ))}
        </div>
      ))}
      <button type="button" className="collapse-btn" onClick={onToggleCollapsed} aria-pressed={collapsed}>
        <Icon d={collapsed ? "M9 6l6 6-6 6" : "M15 6l-6 6 6 6"} />
        <span className="nav-text">{collapsed ? "Expand" : "Collapse"}</span>
      </button>
    </nav>
  );
}
