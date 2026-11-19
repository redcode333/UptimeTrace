/** Text drawn into cached canvas textures for in-scene labels. Labels always carry words, not colour alone. */
import { CanvasTexture, LinearFilter, SRGBColorSpace } from "three";

export interface LabelStyle {
  color?: string;
  background?: string | null;
  border?: string | null;
  borderStyle?: "solid" | "dashed" | "dotted";
  /** Font size in canvas pixels; the sprite's world height is set by the caller. */
  size?: number;
  /** Padding around the text in canvas pixels. Defaults to 45% of the font size. */
  pad?: number;
  weight?: number;
  mono?: boolean;
  /** Second, smaller line. */
  sub?: string | null;
  subColor?: string;
}

export interface Label {
  texture: CanvasTexture;
  /** Width over height, so the sprite keeps its proportions. */
  aspect: number;
}

/**
 * A world-space budget for a label: the largest width and the height it may use, in the same units. When given, the
 * text is shortened with an ellipsis so the label never runs past the space the layout reserved for it.
 */
export interface LabelFit {
  width: number;
  height: number;
}

const cache = new Map<string, Label>();

/** Longest prefix of `s` that, with an ellipsis, fits in `maxPx`. Returns `s` unchanged when it already fits. */
function fitText(ctx: CanvasRenderingContext2D, s: string, maxPx: number): string {
  if (ctx.measureText(s).width <= maxPx) return s;
  let out = s;
  while (out.length > 1 && ctx.measureText(`${out}…`).width > maxPx) out = out.slice(0, -1);
  return `${out}…`;
}

export function labelTexture(text: string, style: LabelStyle = {}, fit?: LabelFit): Label {
  const key = JSON.stringify([text, style, fit ?? null]);
  const hit = cache.get(key);
  if (hit) return hit;
  const size = style.size ?? 40;
  const pad = style.pad ?? Math.round(size * 0.45);
  const font = `${style.weight ?? 600} ${size}px ${style.mono ? '"Spline Sans Mono", ui-monospace, monospace' : '"Public Sans", system-ui, sans-serif'}`;
  const subSize = Math.round(size * 0.68);
  const subFont = `500 ${subSize}px "Public Sans", system-ui, sans-serif`;
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d")!;
  // Height first: it fixes how many pixels one world unit of the budget covers.
  const height = Math.ceil(size + pad * 2 + (style.sub ? subSize + size * 0.3 : 0));
  const maxPx = fit ? Math.floor((fit.width / fit.height) * height) : Number.POSITIVE_INFINITY;
  const inner = maxPx - pad * 2;
  ctx.font = font;
  const shownText = fitText(ctx, text, inner);
  const w1 = ctx.measureText(shownText).width;
  ctx.font = subFont;
  const shownSub = style.sub ? fitText(ctx, style.sub, inner) : null;
  const w2 = shownSub ? ctx.measureText(shownSub).width : 0;
  const width = Math.ceil(Math.min(maxPx, Math.max(w1, w2) + pad * 2));
  canvas.width = width;
  canvas.height = height;
  if (style.background) {
    ctx.fillStyle = style.background;
    roundRect(ctx, 2, 2, width - 4, height - 4, size * 0.3);
    ctx.fill();
  }
  if (style.border) {
    ctx.strokeStyle = style.border;
    ctx.lineWidth = 3;
    ctx.setLineDash(style.borderStyle === "dashed" ? [10, 6] : style.borderStyle === "dotted" ? [3, 5] : []);
    roundRect(ctx, 2, 2, width - 4, height - 4, size * 0.3);
    ctx.stroke();
  }
  ctx.textBaseline = "middle";
  ctx.fillStyle = style.color ?? "#e6eef4";
  ctx.font = font;
  ctx.fillText(shownText, pad, pad + size / 2);
  if (shownSub) {
    ctx.fillStyle = style.subColor ?? "#a3b8c8";
    ctx.font = subFont;
    ctx.fillText(shownSub, pad, pad + size + size * 0.3 + subSize / 2);
  }
  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  texture.minFilter = LinearFilter;
  texture.anisotropy = 4;
  const label = { texture, aspect: width / height };
  cache.set(key, label);
  return label;
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
