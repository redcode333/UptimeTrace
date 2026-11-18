/** Approved navigation (docs/design/PRODUCT_DEFINITION.md). Pages without a backend say so instead of faking data. */
export interface NavItem {
  path: string;
  label: string;
  icon: string; // SVG path data on a 24x24 grid, drawn in one 1.6px stroke
  tag?: string;
  /** Present for pages that are not built or have no backend yet: what they need. */
  unavailable?: string;
}
export interface NavGroup {
  label: string | null;
  items: NavItem[];
}

export const NAV: NavGroup[] = [
  { label: null, items: [{ path: "/", label: "Overview", icon: "M3 11 12 3l9 8M5 10v10h14V10" }] },
  {
    label: "Infrastructure",
    items: [
      { path: "/hosts", label: "Hosts", icon: "M4 5h16v6H4zM4 13h16v6H4zM8 8h.01M8 16h.01" },
      { path: "/metrics", label: "Metrics Explorer", icon: "M4 19V5M4 19h16M8 15l4-5 3 3 5-6" },
      {
        path: "/dependencies", label: "Dependencies", icon: "M6 6h.01M18 6h.01M12 18h.01M6 6l6 12M18 6l-6 12", tag: "soon",
        unavailable: "The dependency map is drawn only from discovered relationships. No discovery source exists yet, so nothing is shown rather than a guessed graph.",
      },
    ],
  },
  {
    label: "Detect & respond",
    items: [
      { path: "/anomalies", label: "Anomalies", icon: "M3 12h4l3-8 4 16 3-8h4", unavailable: "Anomaly detection needs a statistical detector over stored metrics, which is not built yet." },
      { path: "/alerts", label: "Alerts & Rules", icon: "M12 4a5 5 0 0 0-5 5v4l-2 3h14l-2-3V9a5 5 0 0 0-5-5zM10 19a2 2 0 0 0 4 0", unavailable: "Alert rules and their evaluation need the rules engine, which is not built yet." },
      { path: "/incidents", label: "Incidents", icon: "M12 3 2 20h20zM12 10v5M12 18h.01", unavailable: "Incidents need the rules engine and an incident lifecycle, which are not built yet." },
      { path: "/events", label: "Events", icon: "M5 5h14M5 10h14M5 15h9M5 20h6", unavailable: "Events lists system events UptimeTrace generates itself. No event store exists yet, and application logs are not collected." },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { path: "/risk", label: "Risk Outlook", icon: "M4 18c4 0 4-10 8-10s4 10 8 10", tag: "EXP", unavailable: "Risk scores need the model serving and feature pipeline in the backend. The model is experimental and has only been validated on synthetic data." },
      { path: "/investigations", label: "Investigations", icon: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM16 16l5 5", unavailable: "Investigations need an evidence store and a language-model service over structured evidence, which are not built yet." },
    ],
  },
  {
    label: "Settings",
    items: [
      { path: "/settings/agents", label: "Agents", icon: "M8 8h8v8H8zM4 10v4M20 10v4M10 4h4M10 20h4", unavailable: "Agent credential management has no API yet. Credentials are issued with the backend command line." },
      { path: "/settings/workspace", label: "Workspace & members", icon: "M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4 20c0-4 4-6 8-6s8 2 8 6", unavailable: "Workspaces and members are not implemented. Access today is a single-workspace allowlist on the backend." },
    ],
  },
];

export function flatNav(): NavItem[] {
  return NAV.flatMap((g) => g.items);
}

/** Breadcrumb trail for a pathname: [section?, page, detail?]. */
export function breadcrumbs(pathname: string): string[] {
  const detail = pathname.startsWith("/hosts/") ? decodeURIComponent(pathname.slice("/hosts/".length)) : null;
  const hit = flatNav().find((i) => (i.path === "/" ? pathname === "/" : pathname === i.path || pathname.startsWith(i.path + "/")));
  if (!hit) return ["Not found"];
  const group = NAV.find((g) => g.items.includes(hit));
  return [...(group?.label ? [group.label] : []), hit.label, ...(detail ? [detail] : [])];
}
