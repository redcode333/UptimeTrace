/**
 * The 3D infrastructure view: the same inventory, health and alert data as the Topology tab, drawn as a scene.
 * Data and layout are decided in lib/scene3d; this component owns only interaction state (selection, hover, collapse).
 */
import { Component, lazy, Suspense, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { EmptyState, Loading } from "../../../components/states/States";
import { RELATION_STYLES } from "../../../lib/scene3d/connections";
import { healthOverlay, provenanceOverlay } from "../../../lib/scene3d/overlays";
import { registry } from "../../../lib/scene3d/registry";
import { buildSceneFromTopology, isGroup, isHost, isService, type Scene, type SceneEntity } from "../../../lib/scene3d/sceneModel";
import { defaultCollapsed, isContainer, tallyNodes, type Topology } from "../../../lib/topology";
import { CoverageBanners, type Coverage } from "../CoverageBanners";
import { TopologyDetails } from "../TopologyDetails";
import { useModelStatuses, type ModelStatus } from "./useModel";

const InfraCanvas = lazy(() => import("./InfraCanvas"));

interface Props {
  topology: Topology;
  hostCoverage: Coverage | null;
  alertCoverage: Coverage | null;
  onManage: () => void;
  onShowTopology: () => void;
  /** Injected for tests; defaults to a real capability check. */
  webgl?: boolean;
}

export function webglAvailable(): boolean {
  if (typeof window === "undefined" || typeof WebGLRenderingContext === "undefined") return false;
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") ?? c.getContext("webgl"));
  } catch {
    return false;
  }
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

class CanvasBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

type Bulk = "default" | "expanded" | "collapsed";

export default function Infrastructure3DView({ topology, hostCoverage, alertCoverage, onManage, onShowTopology, webgl }: Props) {
  const [bulk, setBulk] = useState<Bulk>("default");
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const [resetToken, setResetToken] = useState(0);
  const [stageEl, setStageEl] = useState<HTMLDivElement | null>(null);
  const canRender = useMemo(() => webgl ?? webglAvailable(), [webgl]);
  const reduced = useMemo(prefersReducedMotion, []);
  const defaults = useMemo(() => defaultCollapsed(topology), [topology]);

  const collapseKey = `${bulk}|${JSON.stringify(toggled)}`;
  const scene: Scene = useMemo(() => {
    const isCollapsed = (id: string): boolean => {
      if (id in toggled) return toggled[id]!;
      if (bulk === "collapsed") return true;
      if (bulk === "expanded") return false;
      return defaults.has(id);
    };
    return buildSceneFromTopology(topology, { isCollapsed });
    // collapseKey captures bulk and toggled
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topology, defaults, collapseKey]);

  const statuses = useModelStatuses(scene.modelUsage);
  const placeholders = statuses.filter((s) => s.source === "placeholder");

  const toggle = (id: string) => {
    const e = scene.entities.get(id);
    if (!isGroup(e) || !isContainer(topology.nodes.get(id))) return;
    setToggled((t) => ({ ...t, [id]: !e.collapsed }));
  };

  if (scene.empty) {
    return (
      <EmptyState title="No infrastructure configured yet" body="The 3D view shows only hosts, groups and services that exist in your inventory. Nothing is drawn from the model library on its own.">
        <button type="button" className="btn" onClick={onManage}>Organise infrastructure</button>{" "}
        <Link className="btn" to="/onboarding">Register a host</Link>
      </EmptyState>
    );
  }

  const all = tallyNodes(topology.roots);
  const sorted = scene.visible.map((id) => scene.entities.get(id)!).filter(Boolean);
  const hoverEntity = hover ? scene.entities.get(hover.id) : undefined;
  const rect = stageEl?.getBoundingClientRect();

  const unsupported = (
    <div className="scene3d-unsupported" role="status">
      <p><b>The 3D view needs WebGL, which is not available in this browser.</b></p>
      <p className="small">The Topology tab shows the same hosts, groups, services, health and alerts as an accessible list.</p>
      <button type="button" className="btn" onClick={onShowTopology}>Open the Topology tab</button>
    </div>
  );

  return (
    <div className="scene3d">
      <div className="topo-summary" aria-label="3D scene summary">
        <span>{topology.hostCount} host{topology.hostCount === 1 ? "" : "s"}</span>
        <span>{topology.services.length} service{topology.services.length === 1 ? "" : "s"}</span>
        <span>{statuses.reduce((n, s) => n + s.instances, 0)} model instances from {statuses.length} model file{statuses.length === 1 ? "" : "s"}</span>
        <span>{all.alerts} active alert{all.alerts === 1 ? "" : "s"}</span>
      </div>

      <CoverageBanners hostCoverage={hostCoverage} alertCoverage={alertCoverage} />
      <PlaceholderNotice placeholders={placeholders} loading={statuses.some((s) => s.source === "loading")} />

      <div className="scene3d-controls">
        <button type="button" className="btn" onClick={() => { setBulk("expanded"); setToggled({}); }}>Expand all</button>
        <button type="button" className="btn" onClick={() => { setBulk("collapsed"); setToggled({}); }}>Collapse all</button>
        <button type="button" className="btn" onClick={() => setResetToken((n) => n + 1)}>Reset view</button>
        <label className="scene3d-find">
          <span className="dim small">Select in scene</span>{" "}
          <select value={selected ?? ""} onChange={(ev) => setSelected(ev.target.value || null)}>
            <option value="">Nothing selected</option>
            {sorted.map((e) => <option key={e.id} value={e.id}>{e.kindLabel}: {e.label}</option>)}
          </select>
        </label>
        {defaults.size > 0 && <span className="dim small">Large hierarchy: clusters start collapsed. Double-click a group to expand it.</span>}
      </div>

      <div className="scene3d-layout">
        <div className="scene3d-stage" ref={setStageEl}>
          {canRender ? (
            <CanvasBoundary fallback={unsupported}>
              <Suspense fallback={<Loading label="Loading the 3D renderer" />}>
                <div className="scene3d-canvas" role="img" aria-label={`3D view of ${topology.hostCount} hosts and ${topology.services.length} services. Use "Select in scene" or the Topology tab to browse them as a list.`}>
                  <InfraCanvas
                    scene={scene}
                    selected={selected}
                    onSelect={setSelected}
                    onHover={(id, at) => setHover(id && at ? { id, ...at } : null)}
                    onToggleCollapse={toggle}
                    resetToken={resetToken}
                    reducedMotion={reduced}
                  />
                </div>
              </Suspense>
            </CanvasBoundary>
          ) : unsupported}
          {hoverEntity && hover && rect && (
            <div className="scene3d-tip" style={{ left: hover.x - rect.left + 14, top: hover.y - rect.top + 14 }} role="tooltip">
              <Tip entity={hoverEntity} />
            </div>
          )}
          <Legend />
        </div>
        <div className="scene3d-side">
          <TopologyDetails topology={topology} selected={selected} select={setSelected} />
          {selected && <ModelPanel scene={scene} id={selected} statuses={statuses} onToggle={toggle} />}
        </div>
      </div>
    </div>
  );
}

function PlaceholderNotice({ placeholders, loading }: { placeholders: ModelStatus[]; loading: boolean }) {
  if (placeholders.length === 0) return loading ? <p className="dim small">Loading models…</p> : null;
  return (
    <div className="banner notice scene3d-placeholder" role="status">
      <b>Development placeholder models.</b> These are temporary stand-ins outlined in cyan, not finished artwork. Supply the model files below to replace them:
      <ul className="plain small">
        {placeholders.map((p) => (
          <li key={p.componentType}><code>{p.url}</code> · {p.displayName} · used by {p.instances} object{p.instances === 1 ? "" : "s"}</li>
        ))}
      </ul>
    </div>
  );
}

function Tip({ entity }: { entity: SceneEntity }) {
  if (isHost(entity)) {
    return (
      <>
        <b>{entity.label}</b> <span className="dim small">Host</span>
        <div className="small" style={{ color: entity.health.color }}>{entity.health.glyph} {entity.health.label}</div>
        <div className="small">{entity.provenance.label} · {entity.provenance.description}</div>
        {entity.alert && <div className="small" style={{ color: entity.alert.color }}>{entity.alert.glyph} {entity.alert.count} active {entity.alert.severity} alert{entity.alert.count === 1 ? "" : "s"}</div>}
      </>
    );
  }
  if (isService(entity)) {
    return <><b>{entity.label}</b> <span className="dim small">Service</span><div className="small">{entity.hostIds.length} host{entity.hostIds.length === 1 ? "" : "s"} assigned</div></>;
  }
  return <><b>{entity.label}</b> <span className="dim small">{entity.kindLabel}</span><div className="small">{entity.aggregate.summary}</div></>;
}

function ModelPanel({ scene, id, statuses, onToggle }: { scene: Scene; id: string; statuses: ModelStatus[]; onToggle: (id: string) => void }) {
  const e = scene.entities.get(id);
  if (!e) return null;
  const def = registry.require(e.componentType);
  const st = statuses.find((s) => s.componentType === e.componentType);
  const links = scene.connections.filter((c) => c.type !== "CONTAINS" && (c.source === id || c.target === id));
  return (
    <aside className="topo-details scene3d-model" aria-label="Scene object">
      <h2 className="topo-details-title">Scene object</h2>
      {def.rendering === "layout-plate" ? (
        <p className="small">Drawn as a <b>{def.display_name}</b> plate sized to what it contains. Scopes do not use a model file.</p>
      ) : (
        <>
          <p className="small">
            Drawn with the <b>{def.display_name}</b> model, shared by {scene.modelUsage.get(e.componentType) ?? 0} object{(scene.modelUsage.get(e.componentType) ?? 0) === 1 ? "" : "s"}.{" "}
            {st?.source === "asset" ? "Supplied model." : st?.source === "placeholder" ? "Development placeholder: the model file is not supplied yet." : "Loading model…"}
          </p>
          <p className="small"><code>{def.asset_path}</code></p>
        </>
      )}
      <p className="small dim">Entity: <code>{e.id}</code>. Health and alerts are drawn as overlays from the monitoring API, never baked into the model.</p>
      {isGroup(e) && <p className="small">{e.aggregate.summary}. A group has no health status of its own.{" "}
        {isContainer(scene.topology.nodes.get(id)) && <button type="button" className="topo-link-btn" onClick={() => onToggle(id)}>{e.collapsed ? "Expand in scene" : "Collapse in scene"}</button>}
      </p>}
      {links.length > 0 && (
        <>
          <p className="small"><b>Relationships</b></p>
          <ul className="plain small">
            {links.map((c) => <li key={c.id}>{RELATION_STYLES[c.type].label}: {scene.entities.get(c.source === id ? c.target : c.source)?.label} <span className="dim">({c.evidence?.detail})</span></li>)}
          </ul>
        </>
      )}
    </aside>
  );
}

function Legend() {
  const states = (["healthy", "warning", "critical", "stale", "offline", "unknown", "unloaded"] as const).map(healthOverlay);
  return (
    <details className="scene3d-legend">
      <summary>Legend</summary>
      <ul className="plain small">
        <li><b>Ring colour and label: host health</b></li>
        {states.map((s) => <li key={s.state}><span style={{ color: s.color }}>{s.glyph}</span> {s.label}{s.pulse !== "none" ? " (pulses)" : ""}</li>)}
        <li><b>Ring pattern and tag: data source</b></li>
        {(["live", "synthetic", "replay"] as const).map((k) => { const p = provenanceOverlay(k); return <li key={k}><span className={`src src-${k}`}>{p.label}</span> {p.pattern} ring · {p.description}</li>; })}
        <li><b>Lines</b></li>
        <li><span style={{ color: RELATION_STYLES.RUNS_ON.color }}>━</span> Runs on: assigned in Infrastructure, not a discovered dependency</li>
        <li>Containment is shown by nesting plates. Nearness means nothing.</li>
        <li>A vertical beam marks a host with an active alert.</li>
      </ul>
    </details>
  );
}
