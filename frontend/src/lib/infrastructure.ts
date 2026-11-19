/**
 * Infrastructure inventory: input validation, server error wording, and the options each control offers. The server remains the
 * authority (backend/app/infrastructure/service.py); these helpers only avoid asking for something the server would refuse.
 */
import { ApiError } from "../api/client";
import type { Inventory, InventoryHost, InventoryRegion } from "../api/types";

export const NAME_MAX = 64;
export const DESCRIPTION_MAX = 500;

/** Trimmed, 1 to 64 characters, no control characters. Mirrors the backend's clean_name. */
export function validateName(raw: string): string | null {
  const name = raw.trim();
  if (name.length < 1 || name.length > NAME_MAX) return `Use 1 to ${NAME_MAX} characters.`;
  if ([...name].some((ch) => ch.charCodeAt(0) < 32)) return "Control characters are not allowed.";
  return null;
}

export function validateDescription(raw: string): string | null {
  return raw.trim().length > DESCRIPTION_MAX ? `At most ${DESCRIPTION_MAX} characters.` : null;
}

const CODE_TEXT: Record<string, string> = {
  name_taken: "A thing with that name already exists at this level. Choose another name.",
  host_in_other_cluster: "That host already belongs to another cluster. Remove it there first.",
  already_in_cluster: "That host is already in this cluster.",
  already_linked: "That service already runs on that host.",
  host_deactivated: "A deactivated host cannot be grouped or linked.",
  not_in_cluster: "That host is not in this cluster.",
  not_linked: "That service does not run on that host.",
  host_not_found: "That host does not exist in this workspace.",
  environment_not_found: "That environment does not exist in this workspace.",
  region_not_found: "That region does not exist in this workspace.",
  cluster_not_found: "That cluster does not exist in this workspace.",
  service_not_found: "That service does not exist in this workspace.",
  invalid_reference: "The change refers to something that is no longer available. Reload and try again.",
};

/** Plain words for a failed change. Nothing about the request is echoed, and nothing was changed on the server. */
export function failureText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.isNetwork) return "Could not reach the UptimeTrace API. Nothing was changed.";
    if (CODE_TEXT[e.code]) return `${CODE_TEXT[e.code]!} Nothing was changed.`;
    const detail = e.details.map((d) => `${d.path}: ${d.message}`).join(" ");
    return `Could not save the change. Nothing was changed.${detail ? ` ${detail}` : ""}`;
  }
  return "The change failed. Nothing was changed.";
}

export function hostLabel(h: InventoryHost): string {
  return h.display_name ? `${h.display_name} (${h.host_id})` : h.host_id;
}

/** Every host the inventory knows about, once each (a host appears either ungrouped or inside one cluster). */
export function allHosts(inv: Inventory): InventoryHost[] {
  const seen = new Map<string, InventoryHost>();
  const add = (h: InventoryHost) => { if (!seen.has(h.host_id)) seen.set(h.host_id, h); };
  inv.ungrouped_hosts.forEach(add);
  const visitRegion = (r: InventoryRegion) => r.clusters.forEach((c) => c.hosts.forEach(add));
  inv.environments.forEach((e) => e.regions.forEach(visitRegion));
  inv.unassigned_regions.forEach(visitRegion);
  inv.unassigned_clusters.forEach((c) => c.hosts.forEach(add));
  return [...seen.values()];
}

/** Hosts a cluster can take: active and not already in a cluster. */
export function clusterAssignableHosts(inv: Inventory): InventoryHost[] {
  return inv.ungrouped_hosts.filter((h) => !h.deactivated);
}

/** Hosts a service can run on: active hosts not already linked to that service. */
export function serviceAssignableHosts(inv: Inventory, serviceHostIds: readonly string[]): InventoryHost[] {
  const linked = new Set(serviceHostIds);
  return allHosts(inv).filter((h) => !h.deactivated && !linked.has(h.host_id));
}

export interface InventorySummary {
  environments: number;
  regions: number;
  clusters: number;
  services: number;
  ungroupedHosts: number;
  groupedHosts: number;
  unassignedServices: number;
}

export function summarize(inv: Inventory): InventorySummary {
  const regionsIn = (rs: InventoryRegion[]) => rs.length;
  const regions = inv.environments.reduce((n, e) => n + e.regions.length, 0) + regionsIn(inv.unassigned_regions);
  const clusterCount = (rs: InventoryRegion[]) => rs.reduce((n, r) => n + r.clusters.length, 0);
  const clusters = inv.environments.reduce((n, e) => n + clusterCount(e.regions), 0) + clusterCount(inv.unassigned_regions) + inv.unassigned_clusters.length;
  const everyHost = allHosts(inv);
  return {
    environments: inv.environments.length,
    regions,
    clusters,
    services: inv.services.length,
    ungroupedHosts: inv.ungrouped_hosts.length,
    groupedHosts: everyHost.length - inv.ungrouped_hosts.length,
    unassignedServices: inv.unassigned_services.length,
  };
}

/** Every cluster with the region it sits in, for a select. Clusters without a region are labelled as such. */
export function clusterChoices(inv: Inventory): { id: string; label: string }[] {
  const out: { id: string; label: string }[] = [];
  const addRegion = (regionLabel: string, r: InventoryRegion) => r.clusters.forEach((c) => out.push({ id: c.id, label: `${c.name} (${regionLabel})` }));
  inv.environments.forEach((e) => e.regions.forEach((r) => addRegion(`${e.name} / ${r.name}`, r)));
  inv.unassigned_regions.forEach((r) => addRegion(r.name, r));
  inv.unassigned_clusters.forEach((c) => out.push({ id: c.id, label: `${c.name} (no region)` }));
  return out;
}

/** Every region with its environment, for a select. */
export function regionChoices(inv: Inventory): { id: string; label: string }[] {
  const out: { id: string; label: string }[] = [];
  inv.environments.forEach((e) => e.regions.forEach((r) => out.push({ id: r.id, label: `${r.name} (${e.name})` })));
  inv.unassigned_regions.forEach((r) => out.push({ id: r.id, label: `${r.name} (no environment)` }));
  return out;
}
