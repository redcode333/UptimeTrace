import { describe, expect, it } from "vitest";
import { applyTransform, checkCompatibility, relationStyle, RELATION_STYLES, resolveAnchor, resolveEndpoints, type SceneConnection } from "./connections";
import { registry } from "./registry";
import { RELATION_TYPES } from "./types";

const close = (a: readonly number[], b: readonly number[]) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i]!, 6));

describe("connection compatibility", () => {
  it.each([
    ["microservice", "physical-server", "RUNS_ON"],
    ["cluster", "physical-server", "CONTAINS"],
    ["region", "cluster", "CONTAINS"],
    ["environment", "region", "CONTAINS"],
    ["backend-application", "postgresql", "READS_FROM"],
    ["event-producer", "kafka-broker", "PUBLISHES_TO"],
    ["uptimetrace-agent", "otel-collector", "SENDS_TELEMETRY_TO"],
    ["load-balancer", "virtual-machine", "ROUTES_TO"],
  ] as const)("allows %s -[%s]-> … %s", (s, t, rel) => {
    expect(checkCompatibility(registry, s, t, rel)).toEqual({ ok: true });
  });

  it.each([
    ["physical-server", "microservice", "RUNS_ON", "Physical server cannot be the source of RUNS_ON"],
    ["physical-server", "region", "CONTAINS", "Physical server cannot be the source of CONTAINS"],
    ["backend-application", "physical-server", "READS_FROM", "Physical server cannot be the target of READS_FROM"],
    ["microservice", "cluster", "RUNS_ON", "Cluster cannot be the target of RUNS_ON"],
    ["router", "postgresql", "PUBLISHES_TO", "Router cannot be the source of PUBLISHES_TO"],
    ["mainframe", "postgresql", "CONNECTS_TO", "Unknown source component mainframe"],
  ] as const)("rejects %s -[%s]-> %s with a reason", (s, t, rel, reason) => {
    expect(checkCompatibility(registry, s, t, rel)).toEqual({ ok: false, reason });
  });

  it("has a configurable visual style for every relation type, with containment drawn as nesting", () => {
    for (const r of RELATION_TYPES) expect(RELATION_STYLES[r]).toBeDefined();
    expect(RELATION_STYLES.CONTAINS.render).toBe("nesting");
    expect(RELATION_STYLES.RUNS_ON.render).toBe("line");
    expect(relationStyle("RUNS_ON", { RUNS_ON: { color: "#fff" } }).color).toBe("#fff");
    // Amber is reserved for forecasts.
    for (const r of RELATION_TYPES) expect(RELATION_STYLES[r].color.toLowerCase()).not.toBe("#f2b64a");
  });
});

describe("anchors and endpoint resolution", () => {
  const server = registry.require("physical-server");
  const svc = registry.require("microservice");

  it("prefers a named anchor from the supplied model over the registry port", () => {
    const a = resolveAnchor(server, "top", new Map([["top", [0, 0.9, 0.1] as const]]));
    expect(a).toEqual({ name: "top", offset: [0, 0.9, 0.1], normal: [0, 1, 0], origin: "model" });
  });

  it("uses the registry port when the model has no such anchor", () => {
    const a = resolveAnchor(server, "top", new Map());
    expect(a.origin).toBe("registry");
    expect(a.offset).toEqual([0, server.footprint[1], 0]);
  });

  it("falls back safely to the top centre when neither exists", () => {
    const a = resolveAnchor(server, "nic_7", null);
    expect(a).toEqual({ name: "fallback", offset: [0, server.footprint[1], 0], normal: [0, 1, 0], origin: "fallback" });
  });

  it("applies scale, rotation and position to anchor offsets", () => {
    close(applyTransform([1, 0, 0], { position: [10, 0, 0], rotation: [0, Math.PI / 2, 0], scale: [2, 2, 2] }), [10, 0, -2]);
    close(applyTransform([0, 1, 0], { position: [0, 0, 0], rotation: [0, 0, Math.PI / 2], scale: [1, 1, 1] }), [-1, 0, 0]);
  });

  it("resolves both ends of a RUNS_ON connection to world points on named ports", () => {
    const conn: SceneConnection = { id: "c", source: "svc:a", target: "host:h", type: "RUNS_ON", sourceAnchor: "bottom", targetAnchor: "top", evidence: null };
    const r = resolveEndpoints(conn, {
      source: { def: svc, transform: { position: [0, 4, -6], rotation: [0, 0, 0], scale: [1, 1, 1] } },
      target: { def: server, transform: { position: [3, 0.3, 0], rotation: [0, 0, 0], scale: [1, 1, 1] }, anchors: new Map([["top", [0, 0.8, 0] as const]]) },
    });
    close(r.from, [0, 4, -6]);
    close(r.to, [3, 1.1, 0]);
    expect(r.source.origin).toBe("registry");
    expect(r.target.origin).toBe("model");
  });
});
