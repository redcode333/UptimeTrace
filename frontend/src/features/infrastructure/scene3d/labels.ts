/** Text drawn into cached canvas textures for in-scene labels. Labels always carry words, not colour alone. */
import { CanvasTexture, LinearFilter, SRGBColorSpace } from "three";

export interface LabelStyle {
  color?: string;
  background?: string | null;
  border?: string | null;
  borderStyle?: "solid" | "dashed" | "dotted";
  /** Font size in canvas pixels; the sprite's world height is set by the caller. */
  size?: number;
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

const cache = new Map<string, Label>();

export function labelTexture(text: string, style: LabelStyle = {}): Label {
  const key = JSON.stringify([text, style]);
  const hit = cache.get(key);
  if (hit) return hit;
  const size = style.size ?? 40;
  const pad = Math.round(size * 0.45);
  const font = `${style.weight ?? 600} ${size}px ${style.mono ? '"Spline Sans Mono", ui-monospace, monospace' : '"Public Sans", system-ui, sans-serif'}`;
  const subSize = Math.round(size * 0.68);
  const subFont = `500 ${subSize}px "Public Sans", system-ui, sans-serif`;
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d")!;
  ctx.font = font;
  const w1 = ctx.measureText(text).width;
  ctx.font = subFont;
  const w2 = style.sub ? ctx.measureText(style.sub).width : 0;
  const width = Math.ceil(Math.max(w1, w2) + pad * 2);
  const height = Math.ceil(size + pad * 2 + (style.sub ? subSize + size * 0.3 : 0));
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
  ctx.fillText(text, pad, pad + size / 2);
  if (style.sub) {
    ctx.fillStyle = style.subColor ?? "#a3b8c8";
    ctx.font = subFont;
    ctx.fillText(style.sub, pad, pad + size + size * 0.3 + subSize / 2);
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
