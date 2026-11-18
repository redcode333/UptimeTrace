import { useCallback, useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

const KEY = "uptimetrace.sidebar.collapsed";

function readCollapsed(): boolean {
  try { return window.localStorage.getItem(KEY) === "1"; } catch { return false; }
}

export function AppShell() {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [drawer, setDrawer] = useState(false);
  const { pathname } = useLocation();

  useEffect(() => setDrawer(false), [pathname]);
  useEffect(() => {
    if (!drawer) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setDrawer(false); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [drawer]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((c) => {
      try { window.localStorage.setItem(KEY, c ? "0" : "1"); } catch { /* storage unavailable: still works in memory */ }
      return !c;
    });
  }, []);

  return (
    <div className={`app${collapsed ? " is-collapsed" : ""}`}>
      {drawer && <div className="scrim" onClick={() => setDrawer(false)} aria-hidden="true" />}
      <Sidebar collapsed={collapsed} open={drawer} onNavigate={() => setDrawer(false)} onToggleCollapsed={toggleCollapsed} />
      <div className="main">
        <TopBar onOpenMenu={() => setDrawer(true)} menuOpen={drawer} />
        <main className="page" id="main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
