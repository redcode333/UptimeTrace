import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Inventory } from "../../api/types";
import { EmptyState } from "../../components/states/States";
import { HealthBadge, SourceBadge } from "../../components/ui/Badges";
import { summarize } from "../../lib/infrastructure";
import {
  defaultCollapsed, hostNodeId, isContainer, tallyNodes, topSeverity, visibleEdges,
  type Topology, type TopoGroup, type TopoHost, type TopoNode, type TopoState,
} from "../../lib/topology";
import { TopologyDetails } from "./TopologyDetails";

export interface Coverage { loaded: number; total: number; complete: boolean }

interface Props {
  inventory: Inventory;
  topology: Topology;
  /** Monitoring records loaded for the hosts. Null when the host list failed. */
  hostCoverage: Coverage | null;
  /** Active alerts loaded. Null when the alert list failed. */
  alertCoverage: Coverage | null;
  onManage: () => void;
}

type Bulk = "default" | "expanded" | "collapsed";
/** Below this width the relationship lines are not drawn; each service card still lists its hosts as text. */
const LINE_MIN_WIDTH = 720;

const KIND_LABEL: Record<TopoGroup["kind"], string> = {
  environment: "Environment",
  region: "Region",
  cluster: "Cluster",
  group: "Group",
};

function stateLabel(state: TopoState): string {
  return state === "unloaded" ? "no data loaded" : state;
}

/** Alert indicator for a host. Only hosts with an active alert get one. */
function AlertIndicator({ host }: { host: TopoHost }) {
  const sev = topSeverity(host.alerts);
  if (!sev) return null;
  const n = host.alerts.length;
  const glyph = sev === "critical" ? "■" : "▲";
  return (
    <span className={`topo-alert ${sev}`} data-testid="host-alert">
      <i aria-hidden="true">{glyph}</i> {n} {sev} alert{n === 1 ? "" : "s"}
    </span>
  );
}

export function TopologyView({ inventory, topology, hostCoverage, alertCoverage, onManage }: Props) {
  const [bulk, setBulk] = useState<Bulk>("default");
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [layoutVersion, setLayoutVersion] = useState(0);
  const defaults = useMemo(() => defaultCollapsed(topology), [topology]);

  const isCollapsed = (id: string): boolean => {
    if (id in toggled) return toggled[id]!;
    if (bulk === "collapsed") return true;
    if (bulk === "expanded") return false;
    return defaults.has(id);
  };

  const toggle = (id: string) => {
    setToggled((t) => ({ ...t, [id]: !isCollapsed(id) }));
    setLayoutVersion((v) => v + 1);
  };
  const expandAll = () => { setBulk("expanded"); setToggled({}); setLayoutVersion((v) => v + 1); };
  const collapseAll = () => { setBulk("collapsed"); setToggled({}); setLayoutVersion((v) => v + 1); };

  const visible = visibleEdges(topology, isCollapsed);
  const edgeKey = visible.map((e) => e.key).join("|");

  // Relationship lines are measured from the rendered rows, so they follow the tree when it expands or the window resizes.
  const containerRef = useRef<HTMLDivElement>(null);
  const [paths, setPaths] = useState<Record<string, string>>({});
  useLayoutEffect(() => {
    const measure = () => {
      const c = containerRef.current;
      if (!c) return;
      if (c.clientWidth < LINE_MIN_WIDTH) { setPaths({}); return; }
      const box = c.getBoundingClientRect();
      const next: Record<string, string> = {};
      for (const e of visible) {
        const from = c.querySelector(`[data-node-id="${e.serviceNodeId}"]`);
        const to = c.querySelector(`[data-node-id="${e.targetNodeId}"]`);
        if (!from || !to) continue;
        const a = to.getBoundingClientRect();
        const b = from.getBoundingClientRect();
        const x1 = a.right - box.left;
        const y1 = a.top + a.height / 2 - box.top;
        const x2 = b.left - box.left;
        const y2 = b.top + b.height / 2 - box.top;
        const mid = (x1 + x2) / 2;
        next[e.key] = `M${x1} ${y1} C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}`;
      }
      setPaths(next);
    };
    measure();
    window.addEventListener("resize", measure);
    const ro = typeof ResizeObserver !== "undefined" && containerRef.current ? new ResizeObserver(measure) : null;
    if (ro && containerRef.current) ro.observe(containerRef.current);
    return () => {
      window.removeEventListener("resize", measure);
      ro?.disconnect();
    };
    // `visible` is derived from topology and the collapse state, which the keys below capture.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topology, edgeKey, layoutVersion]);

  const summary = summarize(inventory);
  const all = tallyNodes(topology.roots);
  const empty = topology.roots.length === 0 && topology.services.length === 0;

  // Host ids under a selected container, so its relationship lines can be highlighted.
  const hostsUnder = (id: string | null): Set<string> => {
    const out = new Set<string>();
    if (!id) return out;
    const walk = (n: TopoNode) => {
      if (n.kind === "host") out.add(n.id);
      else n.children.forEach(walk);
    };
    const n = topology.nodes.get(id);
    if (n) walk(n);
    if (topology.services.some((s) => s.nodeId === id)) {
      topology.services.find((s) => s.nodeId === id)!.hosts.forEach((h) => out.add(h.id));
    }
    return out;
  };
  const highlightedHosts = hostsUnder(selected);

  if (empty) {
    return (
      <EmptyState title="No infrastructure configured yet" body="Hosts appear here once they are registered. Organise them into environments, regions, clusters and services to see how they relate.">
        <button type="button" className="btn" onClick={onManage}>Organise infrastructure</button>{" "}
        <Link className="btn" to="/onboarding">Register a host</Link>
      </EmptyState>
    );
  }

  const renderNode = (node: TopoNode, depth: number): ReactNode => {
    if (node.kind === "host") {
      return (
        <li key={node.id}>
          <div className={`topo-row topo-host${selected === node.id ? " sel" : ""}`} data-node-id={node.id} style={{ paddingLeft: depth * 18 + 26 }}>
            <button type="button" className="topo-select" aria-pressed={selected === node.id} onClick={() => setSelected(node.id)}>
              <span className="topo-label">{node.label}</span>{" "}
              <SourceBadge source={node.source} />{" "}
              {node.state === "unloaded" ? <span className="dim small">no data loaded</span> : <HealthBadge status={node.state} />}{" "}
              <AlertIndicator host={node} />
            </button>
            <Link className="topo-open" to={`/hosts/${encodeURIComponent(node.hostId)}`} aria-label={`Open host detail for ${node.label}`}>Host detail</Link>
          </div>
        </li>
      );
    }
    const container = isContainer(node);
    const collapsed = container && isCollapsed(node.id);
    const count = tallyNodes([node]).hosts;
    return (
      <li key={node.id}>
        <div className={`topo-row topo-group${selected === node.id ? " sel" : ""}`} data-node-id={node.id} style={{ paddingLeft: depth * 18 }}>
          {container ? (
            <button type="button" className="topo-toggle" aria-expanded={!collapsed} aria-label={`${collapsed ? "Expand" : "Collapse"} ${node.label}`} onClick={() => toggle(node.id)}>
              {collapsed ? "▸" : "▾"}
            </button>
          ) : <span className="topo-toggle-spacer" aria-hidden="true" />}
          <button type="button" className="topo-select" aria-pressed={selected === node.id} onClick={() => setSelected(node.id)}>
            <span className="topo-kind">{KIND_LABEL[node.kind]}</span> <b>{node.label}</b>{" "}
            <span className="dim small">{count} host{count === 1 ? "" : "s"}</span>
          </button>
        </div>
        {container && !collapsed && <ul className="topo-children">{node.children.map((c) => renderNode(c, depth + 1))}</ul>}
      </li>
    );
  };

  const edgeOn = (e: { serviceNodeId: string; targetNodeId: string; hostIds: string[] }) =>
    selected !== null && (e.serviceNodeId === selected || e.targetNodeId === selected || e.hostIds.some((h) => highlightedHosts.has(hostNodeId(h))));

  return (
    <div className="topo">
      <div className="topo-summary" aria-label="Topology summary">
        <span>{summary.environments} environment{summary.environments === 1 ? "" : "s"}</span>
        <span>{summary.regions} region{summary.regions === 1 ? "" : "s"}</span>
        <span>{summary.clusters} cluster{summary.clusters === 1 ? "" : "s"}</span>
        <span>{topology.hostCount} host{topology.hostCount === 1 ? "" : "s"} ({topology.groupedHostCount} in a cluster)</span>
        <span>{topology.services.length} service{topology.services.length === 1 ? "" : "s"}</span>
        <span className="topo-tally">
          {(["critical", "warning", "stale", "offline", "unknown", "healthy", "unloaded"] as const)
            .filter((s) => all.tally[s] > 0)
            .map((s) => <span key={s} className={`topo-chip ${s}`}>{stateLabel(s)} {all.tally[s]}</span>)}
        </span>
        <span>{all.alerts} active alert{all.alerts === 1 ? "" : "s"}</span>
      </div>

      {hostCoverage && !hostCoverage.complete && (
        <p className="banner notice" role="status">
          Monitoring data is loaded for {hostCoverage.loaded} of {hostCoverage.total} hosts. Hosts without loaded data show "no data loaded", not a health state.
        </p>
      )}
      {hostCoverage === null && (
        <p className="banner warn" role="alert">Monitoring data could not be loaded. The structure below is still current, but no host health is shown.</p>
      )}
      {alertCoverage && !alertCoverage.complete && (
        <p className="banner notice" role="status">
          Showing {alertCoverage.loaded} of {alertCoverage.total} active alerts. Alert indicators may be incomplete.
        </p>
      )}
      {alertCoverage === null && (
        <p className="banner warn" role="alert">Active alerts could not be loaded. Alert indicators are not shown.</p>
      )}

      <div className="topo-controls">
        <button type="button" className="btn" onClick={expandAll}>Expand all</button>{" "}
        <button type="button" className="btn" onClick={collapseAll}>Collapse all</button>
        {defaults.size > 0 && <span className="dim small"> Large hierarchy: clusters start collapsed.</span>}
      </div>

      <div className="topo-grid" ref={containerRef}>
        <section className="topo-col" aria-label="Containment">
          <h2 className="topo-heading">Environments, regions, clusters and hosts</h2>
          <ul className="topo-tree">{topology.roots.map((r) => renderNode(r, 0))}</ul>
        </section>

        <section className="topo-col" aria-label="Services">
          <h2 className="topo-heading">Services and the hosts they run on</h2>
          {topology.services.length === 0 && <p className="dim small">No services yet. Services you add in Manage appear here with the hosts they run on.</p>}
          <ul className="topo-services">
            {topology.services.map((s) => (
              <li key={s.id}>
                <div className={`topo-svc${selected === s.nodeId ? " sel" : ""}`} data-node-id={s.nodeId}>
                  <button type="button" className="topo-select" aria-pressed={selected === s.nodeId} onClick={() => setSelected(s.nodeId)}>
                    <b>{s.name}</b> <span className="dim small">{s.hosts.length} host{s.hosts.length === 1 ? "" : "s"}</span>
                  </button>
                  {s.hosts.length === 0 ? <p className="dim small">No hosts assigned</p> : (
                    <ul className="topo-svc-hosts">
                      {s.hosts.map((h) => (
                        <li key={h.id}>
                          <button type="button" className="topo-link-btn" onClick={() => setSelected(h.id)}>{h.label}</button>{" "}
                          {h.state === "unloaded" ? <span className="dim small">no data loaded</span> : <HealthBadge status={h.state} />}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>

        <svg className="topo-edges" aria-hidden="true" focusable="false">
          {visible.map((e) => (
            <path
              key={e.key}
              data-testid="topo-edge"
              data-service={e.serviceNodeId}
              data-target={e.targetNodeId}
              data-hosts={e.hostIds.join(",")}
              className={edgeOn(e) ? "on" : undefined}
              d={paths[e.key] ?? ""}
            />
          ))}
        </svg>
      </div>

      <TopologyDetails topology={topology} selected={selected} select={setSelected} />
    </div>
  );
}
