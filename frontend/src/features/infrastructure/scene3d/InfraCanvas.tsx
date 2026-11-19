/**
 * The 3D renderer. It draws a Scene (lib/scene3d/sceneModel.ts) and knows nothing about the API: entities, positions,
 * relationships and overlays all arrive already decided. Health and alert state are overlays around a model, never
 * changes to the model itself.
 */
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { useEffect, useMemo, useRef, type ReactNode } from "react";
import {
  AdditiveBlending, BoxGeometry, BufferGeometry, CircleGeometry, Color, ConeGeometry, CubicBezierCurve3, CylinderGeometry, DoubleSide,
  EdgesGeometry, Float32BufferAttribute, FogExp2, Group, LineBasicMaterial, LineDashedMaterial, LineSegments, Mesh, MeshBasicMaterial,
  MeshStandardMaterial, PlaneGeometry, Quaternion, RingGeometry, SpriteMaterial, TubeGeometry, Vector3,
} from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { relationStyle, resolveEndpoints, type SceneConnection, type Transform } from "../../../lib/scene3d/connections";
import { PLATE_THICKNESS, type Placement } from "../../../lib/scene3d/layout";
import { instantiate } from "../../../lib/scene3d/modelAssets";
import { healthOverlay } from "../../../lib/scene3d/overlays";
import { registry } from "../../../lib/scene3d/registry";
import { isGroup, isHost, isService, type Scene, type SceneEntity, type SceneGroup, type SceneHost, type SceneService, type VisibleLink } from "../../../lib/scene3d/sceneModel";
import type { SceneState, Vec3 } from "../../../lib/scene3d/types";
import { labelTexture, type LabelStyle } from "./labels";
import { LED_NAME } from "./placeholders";
import { useModel } from "./useModel";

export interface InfraCanvasProps {
  scene: Scene;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null, at: { x: number; y: number } | null) => void;
  onToggleCollapse: (id: string) => void;
  /** Changing this number re-fits the camera to the whole scene. */
  resetToken: number;
  reducedMotion: boolean;
}

const GROUND = "#0a1420";

/* ---------- shared, per-state resources (built once; animated in one place) ---------- */

const ledMaterials = new Map<SceneState, MeshStandardMaterial>();
const ringMaterials = new Map<SceneState, MeshBasicMaterial>();
const haloMaterials = new Map<SceneState, MeshBasicMaterial>();
const beaconMaterials = { critical: beamMaterial("#ff6a55"), warning: beamMaterial("#e9b8ff") };

function beamMaterial(color: string) {
  return new MeshBasicMaterial({ color, transparent: true, opacity: 0.22, blending: AdditiveBlending, depthWrite: false, side: DoubleSide });
}
function ledMaterial(state: SceneState) {
  let m = ledMaterials.get(state);
  if (!m) {
    const c = healthOverlay(state).color;
    m = new MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: healthOverlay(state).dim ? 0.35 : 1.6 });
    ledMaterials.set(state, m);
  }
  return m;
}
function ringMaterial(state: SceneState) {
  let m = ringMaterials.get(state);
  if (!m) {
    m = new MeshBasicMaterial({ color: healthOverlay(state).color, transparent: true, opacity: healthOverlay(state).dim ? 0.45 : 0.9, side: DoubleSide, depthWrite: false });
    ringMaterials.set(state, m);
  }
  return m;
}
function haloMaterial(state: SceneState) {
  let m = haloMaterials.get(state);
  if (!m) {
    m = new MeshBasicMaterial({ color: healthOverlay(state).color, transparent: true, opacity: 0.3, blending: AdditiveBlending, side: DoubleSide, depthWrite: false });
    haloMaterials.set(state, m);
  }
  return m;
}

/** Ring patterns are the provenance cue: solid LIVE, dashed SYNTHETIC, dotted REPLAY. Colour is the health cue. */
const ringGeometries = {
  solid: new RingGeometry(0.98, 1.1, 64),
  dashed: mergeGeometries(Array.from({ length: 18 }, (_, i) => new RingGeometry(0.98, 1.1, 6, 1, (i / 18) * Math.PI * 2, (Math.PI * 2) / 18 * 0.6))),
  dotted: mergeGeometries(Array.from({ length: 30 }, (_, i) => {
    const a = (i / 30) * Math.PI * 2;
    return new CircleGeometry(0.05, 10).translate(Math.cos(a) * 1.04, Math.sin(a) * 1.04, 0);
  })),
};
const haloGeometry = new RingGeometry(1.15, 1.6, 64);
const beamGeometry = new CylinderGeometry(0.05, 0.32, 3.4, 20, 1, true).translate(0, 1.7, 0);
const unitBox = new BoxGeometry(1, 1, 1);
const unitEdges = new EdgesGeometry(unitBox);
const selectGeometry = mergeGeometries(Array.from({ length: 4 }, (_, i) => new RingGeometry(1.3, 1.38, 12, 1, (i * Math.PI) / 2 + 0.2, Math.PI / 2 - 0.4)));
const selectMaterial = new MeshBasicMaterial({ color: "#62a8ff", transparent: true, opacity: 0.95, side: DoubleSide, depthWrite: false });
const pulseGeometry = new CircleGeometry(0.11, 16);

const PLATE_STYLE: Record<SceneGroup["kind"], { fill: string; edge: string; opacity: number; label: string }> = {
  environment: { fill: "#0e1b29", edge: "#2c4a63", opacity: 0.95, label: "ENVIRONMENT" },
  region: { fill: "#11233a", edge: "#3b6a92", opacity: 0.92, label: "REGION" },
  cluster: { fill: "#152c44", edge: "#4aa3ff", opacity: 0.9, label: "CLUSTER" },
  group: { fill: "#0c1928", edge: "#7f98ad", opacity: 0.35, label: "UNASSIGNED" },
};

/** One clock for every pulsing overlay, so a fleet of alerts animates with a handful of material updates. */
function SharedPulse({ enabled }: { enabled: boolean }) {
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    const fast = enabled ? 0.5 + 0.5 * Math.sin(t * 6) : 0.6;
    const slow = enabled ? 0.5 + 0.5 * Math.sin(t * 2.2) : 0.6;
    ledMaterial("critical").emissiveIntensity = 0.6 + 2.2 * fast;
    ledMaterial("warning").emissiveIntensity = 0.8 + 1.2 * slow;
    haloMaterial("critical").opacity = 0.12 + 0.38 * fast;
    haloMaterial("warning").opacity = 0.08 + 0.22 * slow;
    beaconMaterials.critical.opacity = 0.12 + 0.28 * fast;
    beaconMaterials.warning.opacity = 0.08 + 0.16 * slow;
    selectMaterial.opacity = enabled ? 0.65 + 0.35 * Math.sin(t * 3) : 0.95;
  });
  return null;
}

/* ---------- small building blocks ---------- */

/**
 * A text sprite. `screen` labels keep a constant on-screen size (height is a fraction of the viewport), so status text
 * stays readable at any zoom; world labels (group names) scale with the scene.
 */
function Label({ text, style, height, position, renderOrder = 10, screen = false }: { text: string; style?: LabelStyle; height: number; position: Vec3; renderOrder?: number; screen?: boolean }) {
  const { texture, aspect } = useMemo(() => labelTexture(text, style), [text, style]);
  const material = useMemo(() => new SpriteMaterial({ map: texture, transparent: true, depthWrite: false, depthTest: !screen, sizeAttenuation: !screen }), [texture, screen]);
  useEffect(() => () => material.dispose(), [material]);
  return <sprite material={material} scale={[height * aspect, height, 1]} position={position as [number, number, number]} renderOrder={renderOrder} />;
}

function useEntrance(index: number, reduced: boolean) {
  const ref = useRef<Group>(null);
  const start = useRef<number | null>(null);
  const delay = Math.min(index * 0.03, 1.2);
  useFrame(({ clock }) => {
    const g = ref.current;
    if (!g) return;
    if (reduced) { g.scale.setScalar(1); return; }
    if (start.current === null) start.current = clock.elapsedTime;
    const p = Math.min(1, Math.max(0, (clock.elapsedTime - start.current - delay) / 0.6));
    const e = 1 - Math.pow(1 - p, 3);
    g.scale.setScalar(Math.max(0.0001, e));
  });
  return ref;
}

interface Handlers {
  onSelect: (id: string | null) => void;
  onHover: InfraCanvasProps["onHover"];
  onToggleCollapse: (id: string) => void;
}

function pointerHandlers(id: string, h: Handlers, collapsible = false) {
  return {
    onClick: (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); h.onSelect(id); },
    onDoubleClick: collapsible ? (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); h.onToggleCollapse(id); } : undefined,
    onPointerOver: (e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); document.body.style.cursor = "pointer"; h.onHover(id, { x: e.nativeEvent.clientX, y: e.nativeEvent.clientY }); },
    onPointerMove: (e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); h.onHover(id, { x: e.nativeEvent.clientX, y: e.nativeEvent.clientY }); },
    onPointerOut: () => { document.body.style.cursor = ""; h.onHover(null, null); },
  };
}

/** A model instance: the supplied GLB, or the identified placeholder, cloned from the shared template. */
function ModelInstance({ componentType, ledState, entityId }: { componentType: string; ledState: SceneState | null; entityId: string }) {
  const model = useModel(componentType);
  const def = registry.require(componentType);
  const object = useMemo(() => {
    if (!model.template) return null;
    const o = instantiate(model.template);
    o.position.set(...(def.default_position as [number, number, number]));
    o.rotation.set(...(def.default_rotation as [number, number, number]));
    o.scale.set(...(def.default_scale as [number, number, number]));
    o.userData.entityId = entityId;
    o.userData.modelSource = model.source;
    if (ledState && model.source === "placeholder") {
      o.traverse((n) => { if (n.name === LED_NAME && n instanceof Mesh) n.material = ledMaterial(ledState); });
    }
    return o;
  }, [model.template, model.source, def, entityId, ledState]);
  if (!object) return null;
  return <primitive object={object} />;
}

/* ---------- hosts ---------- */

function HostNode({ e, placement, index, selected, related, h, reduced }: {
  e: SceneHost; placement: Placement; index: number; selected: boolean; related: boolean; h: Handlers; reduced: boolean;
}) {
  const appear = useEntrance(index, reduced);
  const lift = useRef<Group>(null);
  const halo = useRef<Mesh>(null);
  const hovered = useRef(false);
  const def = registry.require(e.componentType);
  const topY = def.footprint[1];
  const pulse = e.health.pulse;

  useFrame(({ clock }, dt) => {
    const t = clock.elapsedTime;
    if (lift.current) {
      const goal = selected || hovered.current ? 0.18 : 0;
      lift.current.position.y += (goal - lift.current.position.y) * Math.min(1, dt * 10);
    }
    if (halo.current && pulse !== "none") {
      const speed = pulse === "fast" ? 6 : 2.2;
      halo.current.scale.setScalar(reduced ? 1.1 : 1 + 0.18 * (0.5 + 0.5 * Math.sin(t * speed)));
    }
  });

  const hp = pointerHandlers(e.id, {
    ...h,
    onHover: (id, at) => { hovered.current = id !== null; h.onHover(id, at); },
  });
  const statusText = e.health.state === "healthy" ? e.health.glyph : `${e.health.glyph} ${e.health.label}`;
  const prov = e.provenance;

  return (
    <group position={placement.position as [number, number, number]}>
      <group ref={appear}>
        <group ref={lift} {...hp}>
          <ModelInstance componentType={e.componentType} ledState={e.health.state} entityId={e.id} />
          {/* Invisible hit box so the whole footprint is clickable, including gaps in a supplied model. */}
          <mesh geometry={unitBox} scale={[def.footprint[0], def.footprint[1], def.footprint[2]]} position={[0, def.footprint[1] / 2, 0]} visible={false} />
        </group>
        <group rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]} scale={0.95}>
          <mesh geometry={ringGeometries[prov.pattern]} material={ringMaterial(e.health.state)} renderOrder={2} />
          {pulse !== "none" && <mesh ref={halo} geometry={haloGeometry} material={haloMaterial(e.health.state)} renderOrder={1} />}
          {selected && <mesh geometry={selectGeometry} material={selectMaterial} renderOrder={3} />}
        </group>
        <Label
          text={statusText}
          height={e.health.state === "healthy" ? 0.02 : 0.022}
          screen
          position={[0, topY + 0.55, 0]}
          style={{ color: e.health.color, background: "rgba(10,20,32,0.82)", size: 36, weight: 700 }}
        />
        {/* The ring pattern always shows the source; the text tag is always shown for non-live data and on focus for live. */}
        {(!prov.isLive || selected || related) && <Label
          text={prov.label}
          height={0.018}
          screen
          position={[0, 0.16, def.footprint[2] / 2 + 0.38]}
          style={{ color: prov.color, border: prov.color, borderStyle: prov.pattern, background: "rgba(10,20,32,0.8)", size: 30, mono: true }}
        />}
        {(selected || related) && (
          <Label text={e.label} height={0.028} screen position={[0, topY + 1.0, 0]} style={{ background: "rgba(15,29,44,0.92)", border: "#2c4a63", size: 34 }} />
        )}
        {e.alert && (
          <>
            <mesh geometry={beamGeometry} material={beaconMaterials[e.alert.severity]} position={[0, topY, 0]} renderOrder={4} />
            <Label
              text={`${e.alert.glyph} ${e.alert.count} ${e.alert.severity.toUpperCase()} ALERT${e.alert.count === 1 ? "" : "S"}`}
              height={0.026}
              screen
              position={[0, topY + 3.6, 0]}
              style={{ color: e.alert.color, border: e.alert.color, background: "rgba(10,20,32,0.9)", size: 34, weight: 700 }}
            />
          </>
        )}
      </group>
    </group>
  );
}

/* ---------- services ---------- */

function ServiceNode({ e, placement, index, selected, related, h, reduced }: {
  e: SceneService; placement: Placement; index: number; selected: boolean; related: boolean; h: Handlers; reduced: boolean;
}) {
  const appear = useEntrance(index, reduced);
  const bob = useRef<Group>(null);
  const phase = useMemo(() => (index * 1.37) % (Math.PI * 2), [index]);
  useFrame(({ clock }) => {
    if (!bob.current || reduced) return;
    const t = clock.elapsedTime;
    bob.current.position.y = Math.sin(t * 1.2 + phase) * 0.08;
    bob.current.rotation.y = t * 0.25 + phase;
  });
  return (
    <group position={placement.position as [number, number, number]}>
      <group ref={appear}>
        <group ref={bob} {...pointerHandlers(e.id, h)}>
          <ModelInstance componentType={e.componentType} ledState={null} entityId={e.id} />
          <mesh geometry={unitBox} scale={[1.1, 1.1, 1.1]} position={[0, 0.55, 0]} visible={false} />
        </group>
        {selected && (
          <group rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.05, 0]} scale={0.75}>
            <mesh geometry={selectGeometry} material={selectMaterial} />
          </group>
        )}
        <Label
          text={e.label}
          height={0.03}
          screen
          position={[0, 1.55, 0]}
          style={{
            background: selected || related ? "rgba(27,51,72,0.95)" : "rgba(15,29,44,0.88)", border: selected ? "#62a8ff" : "#2c4a63", size: 34,
            sub: e.hostIds.length === 0 ? "no hosts assigned" : `${e.hostIds.length} host${e.hostIds.length === 1 ? "" : "s"} assigned`,
          }}
        />
      </group>
    </group>
  );
}

/* ---------- groups (environment, region, cluster, unassigned) ---------- */

function dashedRect(w: number, d: number): BufferGeometry {
  const pts = [[-w / 2, -d / 2], [w / 2, -d / 2], [w / 2, d / 2], [-w / 2, d / 2], [-w / 2, -d / 2]];
  const pos: number[] = [];
  for (let i = 0; i < 4; i++) pos.push(pts[i]![0]!, 0, pts[i]![1]!, pts[i + 1]![0]!, 0, pts[i + 1]![1]!);
  const g = new BufferGeometry();
  g.setAttribute("position", new Float32BufferAttribute(pos, 3));
  return g;
}

function GroupNode({ e, placement, index, selected, h, reduced }: {
  e: SceneGroup; placement: Placement; index: number; selected: boolean; h: Handlers; reduced: boolean;
}) {
  const appear = useEntrance(index, reduced);
  const [w, d] = placement.size!;
  const s = PLATE_STYLE[e.kind];
  const thick = PLATE_THICKNESS;
  const fill = useMemo(() => new MeshStandardMaterial({ color: s.fill, metalness: 0.2, roughness: 0.85, transparent: s.opacity < 1, opacity: s.opacity }), [s]);
  const edge = useMemo(() => new LineBasicMaterial({ color: selected ? "#62a8ff" : s.edge, transparent: true, opacity: selected ? 1 : 0.8 }), [s, selected]);
  const dashed = useMemo(() => {
    if (!e.unassigned) return null;
    const line = new LineSegments(dashedRect(w, d), new LineDashedMaterial({ color: selected ? "#62a8ff" : s.edge, dashSize: 0.35, gapSize: 0.22 }));
    line.computeLineDistances();
    return line;
  }, [e.unassigned, w, d, s, selected]);
  useEffect(() => () => { fill.dispose(); edge.dispose(); }, [fill, edge]);

  const agg = e.aggregate;
  const sub = e.collapsed
    ? `${agg.summary} · collapsed, double-click to expand`
    : agg.hosts === 0 ? "No hosts" : agg.summary;
  return (
    <group position={placement.position as [number, number, number]}>
      <group ref={appear}>
        <mesh geometry={unitBox} material={fill} scale={[w, thick, d]} position={[0, thick / 2, 0]} receiveShadow {...pointerHandlers(e.id, h, true)} />
        {dashed ? <primitive object={dashed} position={[0, thick + 0.01, 0]} /> : (
          <lineSegments geometry={unitEdges} material={edge} scale={[w, thick, d]} position={[0, thick / 2, 0]} />
        )}
        <Label
          text={`${s.label} · ${e.label}`}
          height={0.75}
          position={[-w / 2 + 0.25 + 0.375 * labelTexture(`${s.label} · ${e.label}`, { size: 34, sub, color: "#e6eef4" }).aspect, thick + 0.45, -d / 2 + 0.5]}
          style={{ size: 34, sub, color: "#e6eef4" }}
        />
        {e.collapsed && <CollapsedStack hosts={agg.hosts} />}
      </group>
    </group>
  );
}

const stackMaterial = new MeshStandardMaterial({ color: "#1b2d40", metalness: 0.5, roughness: 0.4 });
const stackEdge = new LineBasicMaterial({ color: "#3fd6ff", transparent: true, opacity: 0.3 });
function CollapsedStack({ hosts }: { hosts: number }) {
  const layers = Math.min(4, Math.max(1, Math.ceil(hosts / 20)));
  return (
    <group position={[0, PLATE_THICKNESS, 0.2]}>
      {Array.from({ length: layers }, (_, i) => (
        <group key={i} position={[0, 0.2 + i * 0.42, 0]}>
          <mesh geometry={unitBox} material={stackMaterial} scale={[1.6, 0.36, 1.1]} castShadow />
          <lineSegments geometry={unitEdges} material={stackEdge} scale={[1.6, 0.36, 1.1]} />
        </group>
      ))}
      <Label text={`${hosts} host${hosts === 1 ? "" : "s"}`} height={0.028} screen position={[0, 0.5 + layers * 0.42, 0]} style={{ size: 34, background: "rgba(10,20,32,0.85)" }} />
    </group>
  );
}

/* ---------- relationship lines ---------- */

function transformOf(placement: Placement, entity: SceneEntity): Transform {
  const def = registry.require(entity.componentType);
  if (isGroup(entity)) {
    return { position: [placement.position[0], placement.position[1] + PLATE_THICKNESS - def.footprint[1], placement.position[2]], rotation: [0, 0, 0], scale: [1, 1, 1] };
  }
  return {
    position: [placement.position[0] + def.default_position[0], placement.position[1] + def.default_position[1], placement.position[2] + def.default_position[2]],
    rotation: def.default_rotation,
    scale: def.default_scale,
  };
}

function curveGeometry(curve: CubicBezierCurve3, radius: number, dash: readonly [number, number] | null): BufferGeometry {
  if (!dash) return new TubeGeometry(curve, 64, radius, 6, false);
  const length = curve.getLength();
  const parts: BufferGeometry[] = [];
  for (let s = 0; s < length; s += dash[0] + dash[1]) {
    const a = s / length;
    const b = Math.min(1, (s + dash[0]) / length);
    const pts = Array.from({ length: 5 }, (_, i) => curve.getPointAt(a + ((b - a) * i) / 4));
    parts.push(new TubeGeometry(new CubicBezierCurve3(pts[0]!, pts[1]!, pts[3]!, pts[4]!), 4, radius, 6, false));
  }
  return mergeGeometries(parts);
}

function Link({ link, scene, emphasis, reduced }: { link: VisibleLink; scene: Scene; emphasis: "on" | "off" | "normal"; reduced: boolean }) {
  const style = relationStyle(link.type);
  const srcE = scene.entities.get(link.source)!;
  const tgtE = scene.entities.get(link.target)!;
  const srcModel = useModel(srcE.componentType);
  const tgtModel = useModel(tgtE.componentType);
  const pulse = useRef<Mesh>(null);

  const geo = useMemo(() => {
    const sp = scene.layout.placements.get(link.source);
    const tp = scene.layout.placements.get(link.target);
    if (!sp || !tp) return null;
    const conn = scene.connections.find((c) => c.id === link.connectionIds[0]) as SceneConnection;
    const ends = resolveEndpoints(conn, {
      source: { def: registry.require(srcE.componentType), transform: transformOf(sp, srcE), anchors: srcModel.anchors },
      target: { def: registry.require(tgtE.componentType), transform: transformOf(tp, tgtE), anchors: isGroup(tgtE) ? null : tgtModel.anchors },
    });
    const p0 = new Vector3(...ends.from);
    const p3 = new Vector3(...ends.to);
    const k = Math.max(1, p0.distanceTo(p3) * 0.38);
    const curve = new CubicBezierCurve3(p0, p0.clone().add(new Vector3(...ends.fromNormal).multiplyScalar(k)), p3.clone().add(new Vector3(...ends.toNormal).multiplyScalar(k)), p3);
    const tube = curveGeometry(curve, style.width * (link.aggregated ? 1.6 : 1), style.dash);
    const tangent = curve.getTangentAt(1).normalize();
    const arrowQ = new Quaternion().setFromUnitVectors(new Vector3(0, 1, 0), tangent);
    return { curve, tube, arrowAt: p3.clone().addScaledVector(tangent, -0.12), arrowQ };
  }, [link, scene, srcE, tgtE, srcModel.anchors, tgtModel.anchors, style]);

  const material = useMemo(() => {
    const c = new Color(style.color);
    return new MeshBasicMaterial({ color: emphasis === "on" ? c.clone().lerp(new Color("#ffffff"), 0.25) : c, transparent: true, opacity: emphasis === "on" ? 1 : emphasis === "off" ? 0.12 : 0.55, depthWrite: false });
  }, [style, emphasis]);
  useEffect(() => () => material.dispose(), [material]);
  useEffect(() => () => geo?.tube.dispose(), [geo]);

  // A travelling light marks the selected item's relationships. It is emphasis only: it does not represent traffic.
  useFrame(({ clock }) => {
    if (!pulse.current || !geo) return;
    const t = reduced ? 0.5 : (clock.elapsedTime * 0.45) % 1;
    pulse.current.position.copy(geo.curve.getPointAt(t));
  });

  if (!geo) return null;
  return (
    <group>
      <mesh geometry={geo.tube} material={material} renderOrder={5} />
      {style.arrow && <mesh geometry={arrowGeometry} material={material} position={geo.arrowAt} quaternion={geo.arrowQ} renderOrder={5} />}
      {emphasis === "on" && <Billboard><mesh ref={pulse} geometry={pulseGeometry} material={pulseMaterial} renderOrder={6} /></Billboard>}
    </group>
  );
}
const arrowGeometry = new ConeGeometry(0.1, 0.26, 12);
const pulseMaterial = new MeshBasicMaterial({ color: "#cfe6ff", transparent: true, opacity: 0.95, blending: AdditiveBlending, depthWrite: false });

/** Keeps child meshes facing the camera. */
function Billboard({ children }: { children: ReactNode }) {
  const ref = useRef<Group>(null);
  useFrame(({ camera }) => {
    ref.current?.children.forEach((c) => c.quaternion.copy(camera.quaternion));
  });
  return <group ref={ref}>{children}</group>;
}

/* ---------- camera ---------- */

function CameraRig({ scene, selected, resetToken, reduced }: { scene: Scene; selected: string | null; resetToken: number; reduced: boolean }) {
  const { camera, gl } = useThree();
  const controls = useRef<OrbitControls | null>(null);
  const goal = useRef<Vector3 | null>(null);
  const { min, max } = scene.layout.bounds;
  const center = useMemo(() => new Vector3((min[0] + max[0]) / 2, 0, (min[2] + max[2]) / 2), [min, max]);
  const extent = Math.max(max[0] - min[0], max[2] - min[2], 8);
  // Distance at which the scene's bounding sphere fills the vertical field of view, looking down at about 33 degrees.
  const radius = Math.hypot((max[0] - min[0]) / 2, (max[2] - min[2]) / 2, max[1]);
  const fitDistance = Math.max(10, radius / Math.sin(((camera as { fov?: number }).fov ?? 38) * Math.PI / 360) * 0.62);

  useEffect(() => {
    const c = new OrbitControls(camera, gl.domElement);
    c.enableDamping = true;
    c.dampingFactor = 0.08;
    c.maxPolarAngle = Math.PI * 0.47;
    c.minDistance = 3;
    c.maxDistance = 400;
    c.screenSpacePanning = true;
    controls.current = c;
    return () => c.dispose();
  }, [camera, gl]);

  useEffect(() => {
    const dir = new Vector3(0.08, 0.55, 0.83).normalize().multiplyScalar(fitDistance);
    camera.position.set(center.x + dir.x, dir.y, center.z + dir.z);
    controls.current?.target.copy(center);
    controls.current?.update();
    goal.current = null;
    // Re-fit only on reset or when the scene's extent changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetToken, extent, fitDistance, center.x, center.z]);

  useEffect(() => {
    if (!selected) return;
    const p = scene.layout.placements.get(selected);
    if (p) goal.current = new Vector3(p.position[0], p.position[1], p.position[2]);
  }, [selected, scene]);

  useFrame((_, dt) => {
    const c = controls.current;
    if (!c) return;
    if (goal.current) {
      const step = reduced ? 1 : Math.min(1, dt * 3.5);
      const delta = goal.current.clone().sub(c.target).multiplyScalar(step);
      c.target.add(delta);
      camera.position.add(delta);
      if (delta.lengthSq() < 1e-6) goal.current = null;
    }
    c.update();
  });
  return null;
}

/* ---------- environment ---------- */

function Stage({ scene }: { scene: Scene }) {
  const { scene: three } = useThree();
  useEffect(() => {
    three.background = new Color(GROUND);
    three.fog = new FogExp2(GROUND, 0.0085);
  }, [three]);
  const { min, max } = scene.layout.bounds;
  const size = Math.max(max[0] - min[0], max[2] - min[2], 20) * 3;
  const grid = useMemo(() => gridLines(size, 2), [size]);
  return (
    <>
      <hemisphereLight args={["#9fc4ff", "#0a1420", 0.65]} />
      <ambientLight intensity={0.25} />
      <directionalLight position={[18, 30, 22]} intensity={1.6} castShadow shadow-mapSize={[2048, 2048]} shadow-camera-left={-size / 3} shadow-camera-right={size / 3} shadow-camera-top={size / 3} shadow-camera-bottom={-size / 3} />
      <directionalLight position={[-20, 12, -18]} intensity={0.45} color="#4aa3ff" />
      <mesh geometry={floorGeometry} rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.02, 0]} scale={[size, size, 1]} receiveShadow>
        <meshStandardMaterial color="#0b1622" roughness={1} metalness={0} />
      </mesh>
      <primitive object={grid} position={[0, -0.01, 0]} />
    </>
  );
}
const floorGeometry = new PlaneGeometry(1, 1);
function gridLines(size: number, step: number): LineSegments {
  const pos: number[] = [];
  const half = Math.ceil(size / 2 / step) * step;
  for (let v = -half; v <= half; v += step) pos.push(-half, 0, v, half, 0, v, v, 0, -half, v, 0, half);
  const g = new BufferGeometry();
  g.setAttribute("position", new Float32BufferAttribute(pos, 3));
  return new LineSegments(g, new LineBasicMaterial({ color: "#16293b", transparent: true, opacity: 0.6 }));
}

/* ---------- scene ---------- */

function relatedIds(scene: Scene, focus: string | null): Set<string> {
  const out = new Set<string>();
  if (!focus) return out;
  out.add(focus);
  for (const l of scene.links) {
    if (l.source === focus) out.add(l.target);
    if (l.target === focus) out.add(l.source);
  }
  const e = scene.entities.get(focus);
  if (isGroup(e)) {
    const walk = (id: string) => { out.add(id); const c = scene.entities.get(id); if (isGroup(c)) c.childIds.forEach(walk); };
    walk(focus);
    for (const l of scene.links) if (out.has(l.target)) out.add(l.source);
  }
  return out;
}

function SceneContents(props: InfraCanvasProps) {
  const { scene, selected, reducedMotion: reduced } = props;
  const related = useMemo(() => relatedIds(scene, selected), [scene, selected]);
  const h: Handlers = { onSelect: props.onSelect, onHover: props.onHover, onToggleCollapse: props.onToggleCollapse };
  let index = 0;
  return (
    <>
      <Stage scene={scene} />
      <SharedPulse enabled={!reduced} />
      <CameraRig scene={scene} selected={selected} resetToken={props.resetToken} reduced={reduced} />
      {scene.visible.map((id) => {
        const e = scene.entities.get(id);
        const p = scene.layout.placements.get(id);
        if (!e || !p) return null;
        const i = index++;
        if (isHost(e)) return <HostNode key={id} e={e} placement={p} index={i} selected={selected === id} related={related.has(id)} h={h} reduced={reduced} />;
        if (isService(e)) return <ServiceNode key={id} e={e} placement={p} index={i} selected={selected === id} related={related.has(id)} h={h} reduced={reduced} />;
        if (isGroup(e)) return <GroupNode key={id} e={e} placement={p} index={i} selected={selected === id} h={h} reduced={reduced} />;
        return null;
      })}
      {scene.links.map((l) => {
        const on = !!selected && (related.has(l.source) && related.has(l.target)) && (l.source === selected || l.target === selected || isGroup(scene.entities.get(selected)));
        return <Link key={l.key} link={l} scene={scene} emphasis={!selected ? "normal" : on ? "on" : "off"} reduced={reduced} />;
      })}
    </>
  );
}

export default function InfraCanvas(props: InfraCanvasProps) {
  return (
    <Canvas
      shadows="percentage"
      dpr={[1, 2]}
      camera={{ fov: 38, near: 0.1, far: 2000, position: [0, 20, 30] }}
      gl={{ antialias: true, powerPreference: "high-performance" }}
      onPointerMissed={() => props.onSelect(null)}
      onCreated={({ gl }) => { gl.toneMappingExposure = 1.1; }}
    >
      <SceneContents {...props} />
    </Canvas>
  );
}
