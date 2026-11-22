import { describe, expect, it } from "vitest";
import type { Host, LatestMetrics, MetricSample } from "../../api/types";
import { NOT_COLLECTED, buildSignalModel, threadStatus } from "./signalModel";

const NOW = Date.parse("2026-11-20T12:00:00Z");
const FRESH = "2026-11-20T11:59:50Z";
const OLD = "2026-11-20T11:50:00Z";
const THRESHOLD = 60;

function host(id: string, over: Partial<Host> = {}): Host {
  return {
    host_id: id, hostname: `${id}.local`, agent_id: `agent-${id}`, service: null, source: "live", connected_infrastructure: false,
    first_seen: "2026-11-20T11:00:00Z", last_seen: FRESH, last_seen_age_seconds: 10, reporting_status: "reporting",
    health: "healthy", latest: {} as Host["latest"], ...over,
  } as Host;
}

function sample(name: string, value: number, observedAt: string, labels: Record<string, string> = {}, over: Partial<MetricSample> = {}): MetricSample {
  return {
    name, kind: name.includes("bytes") ? "counter" : "gauge", unit: name.includes("bytes") ? "bytes" : "percent",
    labels, value, observed_at: observedAt, stale: false, rate_per_second: null, ...over,
  };
}

function read(hostId: string, metrics: MetricSample[]): LatestMetrics {
  return { host_id: hostId, source: "live", last_seen: FRESH, metrics };
}

describe("buildSignalModel", () => {
  it("draws one lane per host and one block per component the agent reported", () => {
    const m = buildSignalModel([host("h1")], {
      h1: read("h1", [
        sample("system.cpu.utilization", 42, FRESH),
        sample("system.memory.utilization", 61, FRESH),
        sample("system.disk.utilization", 33, FRESH, { mount: "/" }),
        sample("system.disk.utilization", 80, FRESH, { mount: "/data" }),
        sample("system.network.rx_bytes", 1000, FRESH, { interface: "eth0" }, { rate_per_second: 12.5 }),
        sample("system.network.tx_bytes", 900, FRESH, { interface: "eth0" }, { rate_per_second: 7 }),
      ]),
    }, NOW, THRESHOLD);
    expect(m.lanes).toHaveLength(1);
    expect(m.lanes[0]!.components.map((c) => c.label)).toEqual(["CPU", "Disk /", "Disk /data", "eth0 rx", "eth0 tx", "Memory"]); // sorted by label for a stable layout
    expect(m.totalComponents).toBe(6);
  });

  it("uses reported utilisation for block heights and never invents one for counters", () => {
    const m = buildSignalModel([host("h1")], {
      h1: read("h1", [sample("system.cpu.utilization", 150, FRESH), sample("system.network.rx_bytes", 5, FRESH, { interface: "eth0" })]),
    }, NOW, THRESHOLD);
    const byLabel = Object.fromEntries(m.lanes[0]!.components.map((c) => [c.label, c]));
    expect(byLabel["CPU"]!.utilisation).toBe(100);
    expect(byLabel["eth0 rx"]!.utilisation).toBeNull();
  });

  it("marks a host with no sample as having no lane components rather than inventing a thread", () => {
    const m = buildSignalModel([host("h1")], { h1: read("h1", []) }, NOW, THRESHOLD);
    expect(m.lanes[0]!.latestAvailable).toBe(true);
    expect(m.lanes[0]!.components).toEqual([]);
  });

  it("treats a failed or still-loading read as unknown, not as an empty host", () => {
    const m = buildSignalModel([host("h1"), host("h2")], { h1: null, h2: undefined }, NOW, THRESHOLD);
    expect(m.lanes.every((l) => !l.latestAvailable)).toBe(true);
    expect(m.lanes.every((l) => l.components.length === 0)).toBe(true);
  });

  it("classifies threads: flowing, stale sample, silent host, stale host, old sample, and no sample", () => {
    expect(threadStatus(sample("system.cpu.utilization", 1, FRESH), host("a"), NOW, THRESHOLD).status).toBe("flowing");
    expect(threadStatus(sample("system.cpu.utilization", 1, FRESH, {}, { stale: true }), host("a"), NOW, THRESHOLD).status).toBe("stale");
    expect(threadStatus(sample("system.cpu.utilization", 1, FRESH), host("a", { reporting_status: "silent" }), NOW, THRESHOLD).status).toBe("silent");
    expect(threadStatus(sample("system.cpu.utilization", 1, FRESH), host("a", { reporting_status: "stale" }), NOW, THRESHOLD).status).toBe("stale");
    expect(threadStatus(sample("system.cpu.utilization", 1, OLD), host("a"), NOW, THRESHOLD).status).toBe("stale");
    expect(threadStatus(undefined, host("a"), NOW, THRESHOLD).status).toBe("no-sample");
  });

  it("ignores disk and interface samples that carry no volume or interface label", () => {
    const m = buildSignalModel([host("h1")], {
      h1: read("h1", [sample("system.disk.utilization", 5, FRESH), sample("system.network.tx_bytes", 5, FRESH)]),
    }, NOW, THRESHOLD);
    expect(m.lanes[0]!.components).toEqual([]);
  });

  it("lists observed metric families, and the not-collected list is the agent contract with no trace claim", () => {
    const m = buildSignalModel([host("h1")], { h1: read("h1", [sample("system.cpu.utilization", 1, FRESH)]) }, NOW, THRESHOLD);
    expect(m.observedFamilies).toEqual(["CPU utilisation"]);
    expect(NOT_COLLECTED.some((item) => /trace/i.test(item))).toBe(false);
  });

  it("uses the facet counts for the scope chips when they are supplied", () => {
    const m = buildSignalModel([host("h1")], {}, NOW, THRESHOLD, { live: 3, synthetic: 31, replay: 2 });
    expect(m.counts).toEqual({ live: 3, synthetic: 31, replay: 2 });
  });

  it("sorts lanes by hostname so the graph does not reshuffle between refreshes", () => {
    const m = buildSignalModel([host("b", { hostname: "zeta" }), host("a", { hostname: "alpha" })], {}, NOW, THRESHOLD);
    expect(m.lanes.map((l) => l.hostname)).toEqual(["alpha", "zeta"]);
  });
});
