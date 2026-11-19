import { describe, expect, it } from "vitest";
import type { Alert } from "../api/types";
import { DEFAULT_WINDOW_MS, recentTransitions, transitionsComplete, transitionsOf } from "./alertTransitions";

const NOW = new Date("2026-11-19T12:00:00Z");
const at = (h: number, m = 0) => new Date(Date.UTC(2026, 10, 19, h, m)).toISOString().replace(".000Z", "Z");

const alert = (over: Partial<Alert> & { id: string }): Alert => ({
  host_id: "web-1", condition_key: "system.cpu.utilization|{}", rule: "threshold", metric: "system.cpu.utilization", labels: {},
  severity: "critical", state: "open", value: 97, threshold: 95, opened_at: at(10), last_evaluated_at: at(10, 1),
  acknowledged_at: null, acknowledged_by: null, resolved_at: null, resolved_by: null, resolution: null,
  ...over,
});

describe("transitionsOf: only the four documented transitions", () => {
  it("an open alert proves only that it opened", () => {
    expect(transitionsOf(alert({ id: "a" })).map((t) => t.kind)).toEqual(["OPENED"]);
  });

  it("an acknowledged alert adds ACKNOWLEDGED with the acknowledging operator", () => {
    const t = transitionsOf(alert({ id: "a", state: "acknowledged", acknowledged_at: at(10, 5), acknowledged_by: "user_1" }));
    expect(t.map((x) => [x.kind, x.actor])).toEqual([["OPENED", null], ["ACKNOWLEDGED", "user_1"]]);
  });

  it("a system-recovered alert is RECOVERED, by the system, never RESOLVED MANUALLY", () => {
    const t = transitionsOf(alert({ id: "a", state: "resolved", resolved_at: at(10, 20), resolved_by: "system", resolution: "recovered" }));
    expect(t.map((x) => x.kind)).toEqual(["OPENED", "RECOVERED"]);
    expect(t[1]!.actor).toBe("system");
  });

  it("a manually resolved alert is RESOLVED_MANUALLY by the operator, never RECOVERED", () => {
    const t = transitionsOf(alert({ id: "a", state: "resolved", resolved_at: at(10, 30), resolved_by: "user_2", resolution: "manual" }));
    expect(t.map((x) => x.kind)).toEqual(["OPENED", "RESOLVED_MANUALLY"]);
    expect(t[1]!.actor).toBe("user_2");
    expect(t.some((x) => x.kind === "RECOVERED")).toBe(false);
  });

  it("a recovered alert that was acknowledged first yields three transitions in order", () => {
    const t = transitionsOf(alert({
      id: "a", state: "resolved", acknowledged_at: at(10, 5), acknowledged_by: "user_1",
      resolved_at: at(10, 20), resolved_by: "system", resolution: "recovered",
    }));
    expect(t.map((x) => x.kind)).toEqual(["OPENED", "ACKNOWLEDGED", "RECOVERED"]);
  });

  it("a resolved alert without a resolution is not labelled at all", () => {
    const t = transitionsOf(alert({ id: "a", state: "resolved", resolved_at: at(10, 20), resolved_by: null, resolution: null }));
    expect(t.map((x) => x.kind)).toEqual(["OPENED"]);
  });

  it("never emits severity-change, agent or host-state transitions", () => {
    const kinds = new Set(transitionsOf(alert({ id: "a", state: "resolved", acknowledged_at: at(10, 5), acknowledged_by: "u", resolved_at: at(10, 9), resolved_by: "system", resolution: "recovered" })).map((x) => x.kind));
    expect([...kinds].every((k) => ["OPENED", "ACKNOWLEDGED", "RECOVERED", "RESOLVED_MANUALLY"].includes(k))).toBe(true);
  });
});

describe("recentTransitions: window, ordering and limit", () => {
  it("returns transitions newest first", () => {
    const alerts = [
      alert({ id: "a", opened_at: at(9) }),
      alert({ id: "b", opened_at: at(11) }),
    ];
    expect(recentTransitions(alerts, NOW).map((t) => t.alertId)).toEqual(["b", "a"]);
  });

  it("drops transitions older than the 24-hour window", () => {
    const old = new Date(NOW.getTime() - DEFAULT_WINDOW_MS - 60_000).toISOString();
    const recent = new Date(NOW.getTime() - 60_000).toISOString();
    const alerts = [alert({ id: "old", opened_at: old }), alert({ id: "new", opened_at: recent })];
    expect(recentTransitions(alerts, NOW).map((t) => t.alertId)).toEqual(["new"]);
  });

  it("caps the result at the limit, keeping the newest", () => {
    const alerts = Array.from({ length: 30 }, (_, i) => alert({ id: `x${String(i).padStart(2, "0")}`, opened_at: at(11, i % 60) }));
    const out = recentTransitions(alerts, NOW, undefined, 20);
    expect(out).toHaveLength(20);
    expect(out[0]!.at >= out[19]!.at).toBe(true);
  });

  it("breaks equal times by transition order so the output is deterministic", () => {
    const same = at(11);
    const a = alert({ id: "a", opened_at: same, acknowledged_at: same, acknowledged_by: "u" });
    expect(recentTransitions([a], NOW).map((t) => t.kind)).toEqual(["ACKNOWLEDGED", "OPENED"]);
  });
});

describe("transitionsComplete: a capped list never supports an empty claim", () => {
  it("is complete only when every matching alert was returned", () => {
    expect(transitionsComplete(200, 200)).toBe(true);
    expect(transitionsComplete(199, 200)).toBe(false);
    expect(transitionsComplete(200, 201)).toBe(false);
  });
});
