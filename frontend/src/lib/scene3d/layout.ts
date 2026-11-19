/**
 * Deterministic scene layout. Positions are derived only from the topology structure and the collapse state, so the same
 * inventory always produces the same scene. Layout never stores or implies relationships: nesting mirrors containment,
 * and nearness between siblings means nothing.
 */
import { isContainer, type Topology, type TopoNode } from "../topology";
import type { Vec3 } from "./types";

/** Space reserved per host model (x, z), and gaps. */
export const HOST_CELL: readonly [number, number] = [2.2, 2.0];
export const GROUP_PAD = 0.7;
export const LABEL_PAD = 0.8;
export const SIBLING_GAP = 1.0;
export const ROOT_GAP = 3.0;
export const COLLAPSED_SIZE: readonly [number, number] = [3.4, 3.4];
export const EMPTY_SIZE: readonly [number, number] = [3.0, 2.6];
/** Height step per nesting level and plate thickness. */
export const LEVEL_STEP = 0.16;
export const PLATE_THICKNESS = 0.12;
export const SERVICE_SPACING = 3.4;
export const SERVICE_DECK_HEIGHT = 4.2;
export const SERVICE_DECK_OFFSET = 4.5;
export const SERVICES_PER_ROW = 12;

export interface Placement {
  /** Centre of the node's footprint at its base height. */
  position: Vec3;
  /** Footprint (x, z) for containers; null for models. */
  size: readonly [number, number] | null;
  level: number;
}

export interface SceneLayout {
  placements: Map<string, Placement>;
  bounds: { min: Vec3; max: Vec3 };
}

type Size = readonly [number, number];

/** Shelf packing: rows left to right, wrapping at a target width. Returns child offsets from the top-left corner. */
function shelfPack(sizes: readonly Size[], gap: number): { offsets: [number, number][]; width: number; depth: number } {
  if (sizes.length === 0) return { offsets: [], width: 0, depth: 0 };
  const area = sizes.reduce((a, [w, d]) => a + (w + gap) * (d + gap), 0);
  const widest = Math.max(...sizes.map(([w]) => w));
  const target = Math.max(widest, Math.sqrt(area) * 1.25);
  const offsets: [number, number][] = [];
  let x = 0;
  let z = 0;
  let rowDepth = 0;
  let width = 0;
  for (const [w, d] of sizes) {
    if (x > 0 && x + w > target) {
      z += rowDepth + gap;
      x = 0;
      rowDepth = 0;
    }
    offsets.push([x, z]);
    x += w + gap;
    rowDepth = Math.max(rowDepth, d);
    width = Math.max(width, x - gap);
  }
  return { offsets, width, depth: z + rowDepth };
}

export function layoutScene(topology: Topology, isCollapsed: (id: string) => boolean = () => false): SceneLayout {
  const sizes = new Map<string, Size>();
  const childOffsets = new Map<string, [number, number][]>();

  const measure = (n: TopoNode): Size => {
    if (n.kind === "host") return HOST_CELL;
    let size: Size;
    if (!isContainer(n)) size = EMPTY_SIZE;
    else if (isCollapsed(n.id)) size = COLLAPSED_SIZE;
    else {
      const packed = shelfPack(n.children.map(measure), n.children.every((c) => c.kind === "host") ? 0 : SIBLING_GAP);
      childOffsets.set(n.id, packed.offsets);
      size = [packed.width + 2 * GROUP_PAD, packed.depth + 2 * GROUP_PAD + LABEL_PAD];
    }
    sizes.set(n.id, size);
    return size;
  };

  const placements = new Map<string, Placement>();
  const place = (n: TopoNode, left: number, top: number, level: number) => {
    const [w, d] = n.kind === "host" ? HOST_CELL : sizes.get(n.id)!;
    const baseY = level * LEVEL_STEP;
    if (n.kind === "host") {
      placements.set(n.id, { position: [left + w / 2, baseY, top + d / 2], size: null, level });
      return;
    }
    placements.set(n.id, { position: [left + w / 2, baseY, top + d / 2], size: [w, d], level });
    const offs = childOffsets.get(n.id);
    if (!offs) return;
    n.children.forEach((c, i) => {
      const [ox, oz] = offs[i]!;
      // Children sit one level up, inside the padding, below the label strip at the back edge.
      place(c, left + GROUP_PAD + ox, top + GROUP_PAD + LABEL_PAD + oz, c.kind === "host" ? level + 1 : level + 1);
    });
  };

  const rootSizes = topology.roots.map(measure);
  const packed = shelfPack(rootSizes, ROOT_GAP);
  topology.roots.forEach((r, i) => place(r, packed.offsets[i]![0], packed.offsets[i]![1], 0));

  // Centre the containment area on the origin.
  const cx = packed.width / 2;
  const cz = packed.depth / 2;
  for (const [id, p] of placements) placements.set(id, { ...p, position: [p.position[0] - cx, p.position[1], p.position[2] - cz] });

  // Services: a raised deck behind the containment area, ordered by where their hosts are, so lines cross less.
  const meanX = (hostNodeIds: string[]) => {
    const xs = hostNodeIds.map((id) => placementOfVisible(topology, placements, id)?.position[0]).filter((x): x is number => x !== undefined);
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : Number.POSITIVE_INFINITY;
  };
  const ordered = [...topology.services]
    .map((s) => ({ s, x: meanX(s.hosts.map((h) => h.id)) }))
    .sort((a, b) => (a.x === b.x ? a.s.name.localeCompare(b.s.name) : a.x - b.x));
  const deckZ = -cz - SERVICE_DECK_OFFSET;
  ordered.forEach(({ s }, i) => {
    const row = Math.floor(i / SERVICES_PER_ROW);
    const inRow = Math.min(SERVICES_PER_ROW, ordered.length - row * SERVICES_PER_ROW);
    const col = i % SERVICES_PER_ROW;
    const x = (col - (inRow - 1) / 2) * SERVICE_SPACING;
    placements.set(s.nodeId, { position: [x, SERVICE_DECK_HEIGHT + row * 1.6, deckZ - row * 2.4], size: null, level: 0 });
  });

  const min: [number, number, number] = [Infinity, 0, Infinity];
  const max: [number, number, number] = [-Infinity, 0, -Infinity];
  for (const p of placements.values()) {
    const [hw, hd] = p.size ? [p.size[0] / 2, p.size[1] / 2] : [HOST_CELL[0] / 2, HOST_CELL[1] / 2];
    min[0] = Math.min(min[0], p.position[0] - hw);
    min[2] = Math.min(min[2], p.position[2] - hd);
    max[0] = Math.max(max[0], p.position[0] + hw);
    max[2] = Math.max(max[2], p.position[2] + hd);
    max[1] = Math.max(max[1], p.position[1] + 1.5);
  }
  if (placements.size === 0) return { placements, bounds: { min: [-5, 0, -5], max: [5, 2, 5] } };
  return { placements, bounds: { min, max } };
}

/** Placement of a node, or of the collapsed container that currently stands in for it. */
function placementOfVisible(topology: Topology, placements: Map<string, Placement>, nodeId: string): Placement | undefined {
  let cur: string | null | undefined = nodeId;
  while (cur) {
    const p = placements.get(cur);
    if (p) return p;
    cur = topology.parent.get(cur) ?? null;
  }
  return undefined;
}
