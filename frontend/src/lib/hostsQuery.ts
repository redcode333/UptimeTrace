/** URL <-> inventory filter state for the Hosts page. Source scope is NOT here: it is the global control (lib/scope). */
import type { HealthStatus } from "../api/types";

export const HEALTHS: readonly HealthStatus[] = ["healthy", "warning", "critical", "stale", "offline", "unknown"];
export const PAGE_SIZE = 25;

/** Registry group filters: ids of environments, regions, clusters and services. A host must match each dimension that is set. */
export const GROUP_KEYS = ["environment", "region", "cluster", "service"] as const;
export type GroupKey = (typeof GROUP_KEYS)[number];

export interface HostsView {
  search: string;
  health: HealthStatus[];
  environment: string[];
  region: string[];
  cluster: string[];
  service: string[];
  sort: "last_seen" | "hostname" | "attention";
  order: "asc" | "desc";
  page: number;
}

const csv = (raw: string | null): string[] => [...new Set((raw ?? "").split(",").map((s) => s.trim()).filter(Boolean))];

export function parseHostsView(p: URLSearchParams): HostsView {
  const wanted = new Set(csv(p.get("health")).map((s) => s.toLowerCase()));
  const raw = p.get("sort");
  const sort: HostsView["sort"] = raw === "hostname" || raw === "attention" ? raw : "last_seen";
  const order = p.get("order") === "asc" || p.get("order") === "desc" ? (p.get("order") as "asc" | "desc") : sort === "last_seen" ? "desc" : "asc";
  const page = Math.max(1, Number.parseInt(p.get("page") ?? "1", 10) || 1);
  return {
    search: p.get("search") ?? "", health: HEALTHS.filter((h) => wanted.has(h)), sort, order, page,
    environment: csv(p.get("environment")), region: csv(p.get("region")), cluster: csv(p.get("cluster")), service: csv(p.get("service")),
  };
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
  for (const k of GROUP_KEYS) set(k, next[k].length ? next[k].join(",") : null);
  set("sort", next.sort === "last_seen" ? null : next.sort);
  const defOrder = next.sort === "last_seen" ? "desc" : "asc";
  set("order", next.order === defOrder ? null : next.order);
  set("page", next.page > 1 ? String(next.page) : null);
  return out;
}
