import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import type { Inventory, InventoryHost } from "../api/types";
import {
  allHosts, clusterAssignableHosts, clusterChoices, failureText, serviceAssignableHosts, summarize, validateDescription, validateName,
} from "./infrastructure";

const h = (host_id: string, extra: Partial<InventoryHost> = {}): InventoryHost => ({
  host_id, display_name: null, kind: "live", deactivated: false, ...extra,
});

const inv: Inventory = {
  environments: [{
    id: "e1", name: "production",
    regions: [{ id: "r1", name: "eu-west-1", clusters: [{ id: "c1", name: "api", hosts: [h("api-1"), h("api-2", { kind: "synthetic" })] }] }],
  }],
  unassigned_regions: [],
  unassigned_clusters: [{ id: "c2", name: "batch", hosts: [h("batch-1")] }],
  ungrouped_hosts: [h("loose-1"), h("retired-1", { deactivated: true })],
  services: [{ id: "s1", name: "checkout", description: null, hosts: [h("api-1")] }],
  unassigned_services: [{ id: "s2", name: "metrics" }],
};

describe("name and description validation mirrors the server", () => {
  it("accepts 1 to 64 characters after trimming", () => {
    expect(validateName("  api  ")).toBeNull();
    expect(validateName("   ")).not.toBeNull();
    expect(validateName("x".repeat(64))).toBeNull();
    expect(validateName("x".repeat(65))).not.toBeNull();
  });

  it("rejects control characters", () => {
    expect(validateName("bad\tname")).toBe("Control characters are not allowed.");
  });

  it("limits descriptions to 500 characters", () => {
    expect(validateDescription("y".repeat(500))).toBeNull();
    expect(validateDescription("y".repeat(501))).not.toBeNull();
  });
});

describe("server errors become plain words", () => {
  it("maps known codes and never echoes request content", () => {
    expect(failureText(new ApiError(409, "host_in_other_cluster", "x"))).toBe(
      "That host already belongs to another cluster. Remove it there first. Nothing was changed.");
    expect(failureText(new ApiError(409, "name_taken", "x"))).toMatch(/already exists/);
  });

  it("says nothing was changed when the network is down", () => {
    expect(failureText(new ApiError(0, "network", "x"))).toBe("Could not reach the UptimeTrace API. Nothing was changed.");
  });

  it("falls back to field details for unknown validation errors", () => {
    const text = failureText(new ApiError(422, "invalid_request", "x", [{ path: "name", message: "Between 1 and 64 characters." }]));
    expect(text).toContain("name: Between 1 and 64 characters.");
  });

  it("handles non-API failures", () => {
    expect(failureText(new Error("boom"))).toBe("The change failed. Nothing was changed.");
  });
});

describe("inventory helpers", () => {
  it("lists every host once, grouped or not", () => {
    expect(allHosts(inv).map((x) => x.host_id).sort()).toEqual(["api-1", "api-2", "batch-1", "loose-1", "retired-1"]);
  });

  it("offers only active ungrouped hosts to a cluster", () => {
    expect(clusterAssignableHosts(inv).map((x) => x.host_id)).toEqual(["loose-1"]);
  });

  it("offers a service only hosts it does not already run on, and never deactivated ones", () => {
    expect(serviceAssignableHosts(inv, ["api-1"]).map((x) => x.host_id).sort()).toEqual(["api-2", "batch-1", "loose-1"]);
  });

  it("summarises counts without double counting grouped hosts", () => {
    expect(summarize(inv)).toEqual({
      environments: 1, regions: 1, clusters: 2, services: 1, ungroupedHosts: 2, groupedHosts: 3, unassignedServices: 1,
    });
  });

  it("labels cluster and region choices by their parents", () => {
    expect(clusterChoices(inv).map((c) => c.label)).toEqual(["api (production / eu-west-1)", "batch (no region)"]);
  });
});
