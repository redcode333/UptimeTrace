import { describe, expect, it } from "vitest";
import type { Alert, Host, HostList } from "../api/types";
import { host, hostList } from "../test/fixtures";
import {
  ALL_SOURCES, connectedSummary, coverage, hostCoverage, indexHosts, isAllSelected, metricState, reasonText, sortAttention,
  summarizeAlerts, tallyStates,
} from "./commandCenter";

const alert = (over: Partial<Alert> & { id: string; host_id: string }): Alert => ({
  condition_key: "system.cpu.utilization|{}", rule: "threshold", metric: "system.cpu.utilization", labels: {}, severity: "critical",
  state: "open", value: 97, threshold: 95, opened_at: "2026-11-19T10:00:00Z", last_evaluated_at: "2026-11-19T10:01:00Z",
  acknowledged_at: null, acknowledged_by: null, resolved_at: null, resolved_by: null, resolution: null,
  ...over,
});

const hostsWith = (n: number, source: "live" | "synthetic" = "live") =>
  Array.from({ length: n }, (_, i) => host({ host_id: `h-${i}`, source, connected_infrastructure: source === "live" }));

describe("coverage", () => {
  it("is complete only when every reported row was returned", () => {
    expect(coverage(199, 200).complete).toBe(false);
    expect(coverage(200, 200).complete).toBe(true);
    expect(coverage(200, 201).complete).toBe(false);
    expect(coverage(0, 0).complete).toBe(true);
  });

  it("reads host list completeness from items and total", () => {
    // Boundary: 199, 200 and 201 hosts reported; the page holds at most 200 rows.
    expect(hostCoverage(hostList(hostsWith(199), { total: 199 }))?.complete).toBe(true);
    expect(hostCoverage(hostList(hostsWith(200), { total: 200 }))?.complete).toBe(true);
    expect(hostCoverage(hostList(hostsWith(200), { total: 201 }))?.complete).toBe(false);
    expect(hostCoverage(undefined)).toBeNull();
  });

  it("treats every selection of all sources as no subset", () => {
    expect(isAllSelected(ALL_SOURCES)).toBe(true);
    expect(isAllSelected(["live"])).toBe(false);
  });
});

describe("summarizeAlerts: exact versus partial", () => {
  const hosts = [host({ host_id: "live-1", source: "live" }), host({ host_id: "sim-1", source: "synthetic", connected_infrastructure: false })];
  const idx = indexHosts(hosts);

  it("is exact for every source when the alert list is complete", () => {
    const alerts = [alert({ id: "a", host_id: "live-1" }), alert({ id: "b", host_id: "sim-1", severity: "warning" })];
    const s = summarizeAlerts(alerts, 2, ALL_SOURCES, null, false);
    expect(s.scopeExact).toBe(true);
    expect(s.joinNeeded).toBe(false);
    expect(s.bySeverity).toEqual({ critical: 1, warning: 1 });
    expect(s.totalActive).toBe(2);
  });

  it("is partial when the alert list is capped, even with every source selected", () => {
    const alerts = [alert({ id: "a", host_id: "live-1" })];
    const s = summarizeAlerts(alerts, 250, ALL_SOURCES, null, false);
    expect(s.alertsCapped).toBe(true);
    expect(s.scopeExact).toBe(false);
    expect(s.totalActive).toBe(250); // the exact total is still reported
    expect(s.loaded).toBe(1);
  });

  it("filters by the selected source using the host index, and is exact when every host resolves", () => {
    const alerts = [alert({ id: "a", host_id: "live-1" }), alert({ id: "b", host_id: "sim-1" })];
    const s = summarizeAlerts(alerts, 2, ["live"], idx, true);
    expect(s.inScope.map((a) => a.id)).toEqual(["a"]);
    expect(s.notLoaded).toBe(0);
    expect(s.scopeExact).toBe(true);
  });

  it("counts an alert whose host is missing from the loaded list as not loaded, never as in scope", () => {
    const alerts = [alert({ id: "a", host_id: "live-1" }), alert({ id: "ghost", host_id: "unknown-host" })];
    const s = summarizeAlerts(alerts, 2, ["live"], idx, true);
    expect(s.notLoaded).toBe(1);
    expect(s.inScope.map((a) => a.id)).toEqual(["a"]);
    expect(s.scopeExact).toBe(false);
  });

  it("is partial when the host list is capped, even if every alert resolves", () => {
    const alerts = [alert({ id: "a", host_id: "live-1" })];
    const s = summarizeAlerts(alerts, 1, ["live"], idx, false);
    expect(s.notLoaded).toBe(0);
    expect(s.scopeExact).toBe(false);
  });

  it("with no host index for a subset, nothing is attributed to a source", () => {
    const alerts = [alert({ id: "a", host_id: "live-1" })];
    const s = summarizeAlerts(alerts, 1, ["live"], null, false);
    expect(s.inScope).toHaveLength(0);
    expect(s.notLoaded).toBe(1);
    expect(s.scopeExact).toBe(false);
  });

  it("orders the top alerts critical first, then newest", () => {
    const alerts = [
      alert({ id: "w-new", host_id: "live-1", severity: "warning", opened_at: "2026-11-19T11:00:00Z" }),
      alert({ id: "c-old", host_id: "live-1", severity: "critical", opened_at: "2026-11-19T09:00:00Z" }),
      alert({ id: "c-new", host_id: "live-1", severity: "critical", opened_at: "2026-11-19T10:30:00Z" }),
    ];
    const s = summarizeAlerts(alerts, 3, ALL_SOURCES, null, false);
    expect(s.top.map((a) => a.id)).toEqual(["c-new", "c-old", "w-new"]);
  });

  it("counts rules as well as severities", () => {
    const alerts = [alert({ id: "a", host_id: "live-1" }), alert({ id: "b", host_id: "live-1", rule: "host_offline", metric: null, labels: {} })];
    const s = summarizeAlerts(alerts, 2, ALL_SOURCES, null, false);
    expect(s.byRule).toEqual({ threshold: 1, host_offline: 1 });
  });
});

describe("connectedSummary", () => {
  it("derives LIVE-only counts from the live facets", () => {
    const list: Pick<HostList, "facets"> = {
      facets: { source: { live: 3, synthetic: 5, replay: 0 }, health: { healthy: 1, warning: 1, critical: 0, stale: 0, offline: 1, unknown: 0 } },
    };
    expect(connectedSummary(list)).toEqual({ live: 3, reporting: 2, silent: 1, stale: 0, unknown: 0 });
  });
});

describe("reasonText", () => {
  it("names the first reason with its value and limit", () => {
    const h = host({
      host_id: "web-1",
      health: { status: "critical", category: "degraded", reasons: [{ metric: "system.disk.utilization", labels: { mount: "C:" }, value: 98.2, threshold: 95, level: "critical", key: "k" }] },
    });
    expect(reasonText(h)).toBe("disk C: 98.2% ≥ 95%");
  });

  it("says how many more reasons there are", () => {
    const r = { metric: "system.cpu.utilization", labels: {}, value: 97, threshold: 95, level: "critical" as const, key: "k" };
    const h = host({ host_id: "x", health: { status: "critical", category: "degraded", reasons: [r, { ...r, metric: "system.memory.utilization" }] } });
    expect(reasonText(h)).toMatch(/\+1 more$/);
  });

  it("describes stale and offline hosts by age, never by a metric value", () => {
    expect(reasonText(host({ host_id: "s", health: { status: "stale", category: "stale", reasons: [] }, last_seen_age_seconds: 120 }))).toBe("Telemetry 2m old");
    expect(reasonText(host({ host_id: "o", health: { status: "offline", category: "offline", reasons: [] }, last_seen_age_seconds: 4000 }))).toBe("No telemetry for 1h 6m");
  });

  it("says a unknown host has no fresh measurement", () => {
    expect(reasonText(host({ host_id: "u", health: { status: "unknown", category: "unknown", reasons: [] } }))).toBe("No fresh CPU, memory or disk measurement");
  });
});

describe("sortAttention", () => {
  it("orders by health severity, then hostname, over the loaded rows only", () => {
    const rows: Host[] = [
      host({ host_id: "b", hostname: "b", health: { status: "warning", category: "degraded", reasons: [] } }),
      host({ host_id: "a", hostname: "a", health: { status: "offline", category: "offline", reasons: [] } }),
      host({ host_id: "c", hostname: "c", health: { status: "critical", category: "degraded", reasons: [] } }),
    ];
    expect(sortAttention(rows).map((h) => h.hostname)).toEqual(["c", "b", "a"]);
  });
});

describe("configured thresholds", () => {
  const rule = { warning: 85, critical: 95 };
  it("classifies at the configured limits, inclusive, exactly as the health states do", () => {
    expect(metricState(96, rule)).toBe("critical");
    expect(metricState(95, rule)).toBe("critical");
    expect(metricState(94.9, rule)).toBe("warning");
    expect(metricState(85, rule)).toBe("warning");
    expect(metricState(84.9, rule)).toBe("normal");
  });

  it("never classifies a missing value or a missing limit", () => {
    expect(metricState(null, rule)).toBe("none");
    expect(metricState(undefined, rule)).toBe("none");
    expect(metricState(99, undefined)).toBe("none");
  });

  it("tallies a fleet, counting missing values separately and never as normal", () => {
    expect(tallyStates([96, 50, null, 88], rule)).toEqual({ normal: 1, warning: 1, critical: 1, none: 1 });
  });
});

