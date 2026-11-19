/** URL <-> inventory filter state for the Hosts page. Source scope is NOT here: it is the global control (lib/scope). */
import type { HealthStatus } from "../api/types";

export const HEALTHS: readonly HealthStatus[] = ["healthy", "warning", "critical", "stale", "offline", "unknown"];
export const PAGE_SIZE = 25;

export interface HostsView {
  search: string;
  health: HealthStatus[];
  sort: "last_seen" | "hostname";
  order: "asc" | "desc";
  page: number;
}

export function parseHostsView(p: URLSearchParams): HostsView {
  const wanted = new Set((p.get("health") ?? "").split(",").map((s) => s.trim().toLowerCase()));
  const sort = p.get("sort") === "hostname" ? "hostname" : "last_seen";
  const order = p.get("order") === "asc" || p.get("order") === "desc" ? (p.get("order") as "asc" | "desc") : sort === "hostname" ? "asc" : "desc";
  const page = Math.max(1, Number.parseInt(p.get("page") ?? "1", 10) || 1);
  return { search: p.get("search") ?? "", health: HEALTHS.filter((h) => wanted.has(h)), sort, order, page };
}

/** Writes a view back, dropping defaults. Any filter change other than `page` resets to page 1. */
export function applyHostsView(base: URLSearchParams, patch: Partial<HostsView>): URLSearchParams {
  const cur = parseHostsView(base);
  const next = { ...cur, ...patch };
  if (!("page" in patch)) next.page = 1;
  const out = new URLSearchParams(base);
  const set = (k: string, v: string | null) => (v ? out.set(k, v) : out.delete(k));
  set("search", next.search.trim() || null);
  set("health", next.health.length ? next.health.join(",") : null);
  set("sort", next.sort === "last_seen" ? null : next.sort);
  const defOrder = next.sort === "hostname" ? "asc" : "desc";
  set("order", next.order === defOrder ? null : next.order);
  set("page", next.page > 1 ? String(next.page) : null);
  return out;
}
