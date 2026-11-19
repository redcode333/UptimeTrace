import { describe, expect, it } from "vitest";
import type { Alert } from "../api/types";
import { acknowledgementText, alertsCapped, conditionText, resolutionText, ruleFor, splitHostAlerts, thresholdText } from "./hostDetail";

const alert = (over: Partial<Alert> & { id: string }): Alert => ({
  host_id: "web-1", condition_key: "system.cpu.utilization|{}", rule: "threshold", metric: "system.cpu.utilization", labels: {},
  severity: "critical", state: "open", value: 97, threshold: 95, opened_at: "2026-11-19T10:00:00Z", last_evaluated_at: "2026-11-19T10:01:00Z",
  acknowledged_at: null, acknowledged_by: null, resolved_at: null, resolved_by: null, resolution: null, ...over,
});

describe("configured thresholds", () => {
  const rules = { "system.cpu.utilization": { warning: 80, critical: 90 } };

  it("returns the configured rule for a metric", () => {
    expect(ruleFor(rules, "system.cpu.utilization")).toEqual({ warning: 80, critical: 90 });
  });

  it("never substitutes a default when a rule is missing", () => {
    expect(ruleFor(rules, "system.disk.utilization")).toBeUndefined();
    expect(ruleFor(undefined, "system.cpu.utilization")).toBeUndefined();
    expect(thresholdText(undefined)).toBeNull();
  });

  it("states the configured limits in words", () => {
    expect(thresholdText({ warning: 80, critical: 90 })).toBe("warning ≥ 80% · critical ≥ 90%");
  });
});

describe("splitHostAlerts", () => {
  it("keeps active alerts apart from resolved ones", () => {
    const out = splitHostAlerts([
      alert({ id: "a" }),
      alert({ id: "r", state: "resolved", resolved_at: "2026-11-19T11:00:00Z", resolved_by: "system", resolution: "recovered" }),
    ]);
    expect(out.active.map((a) => a.id)).toEqual(["a"]);
    expect(out.resolved.map((a) => a.id)).toEqual(["r"]);
  });

  it("orders active alerts critical first, then newest", () => {
    const out = splitHostAlerts([
      alert({ id: "w", severity: "warning", opened_at: "2026-11-19T11:00:00Z" }),
      alert({ id: "c-old", opened_at: "2026-11-19T09:00:00Z" }),
      alert({ id: "c-new", opened_at: "2026-11-19T10:30:00Z" }),
    ]);
    expect(out.active.map((a) => a.id)).toEqual(["c-new", "c-old", "w"]);
  });

  it("orders resolved alerts most recently resolved first and reports how many were hidden", () => {
    const resolved = Array.from({ length: 12 }, (_, i) => alert({
      id: `r${i}`, state: "resolved", resolved_at: `2026-11-19T10:${String(i).padStart(2, "0")}:00Z`, resolved_by: "system", resolution: "recovered",
    }));
    const out = splitHostAlerts(resolved, 10);
    expect(out.resolved).toHaveLength(10);
    expect(out.resolved[0]!.id).toBe("r11");
    expect(out.resolvedHidden).toBe(2);
  });
});

describe("resolution and acknowledgement wording", () => {
  it("names system recovery and manual closure differently", () => {
    expect(resolutionText(alert({ id: "a", state: "resolved", resolution: "recovered", resolved_by: "system" }))).toBe("Recovered by telemetry");
    expect(resolutionText(alert({ id: "b", state: "resolved", resolution: "manual", resolved_by: "user_2" }))).toBe("Resolved manually by user_2");
  });

  it("never claims a recovery when the resolution was not recorded", () => {
    expect(resolutionText(alert({ id: "c", state: "resolved", resolution: null }))).toBe("Resolved (how it ended was not recorded)");
  });

  it("states the acknowledgement of an open alert", () => {
    expect(acknowledgementText(alert({ id: "d" }))).toBe("Not acknowledged");
    expect(acknowledgementText(alert({ id: "e", state: "acknowledged", acknowledged_by: "user_1" }))).toBe("Acknowledged by user_1");
    expect(acknowledgementText(alert({ id: "f", state: "resolved", resolution: "recovered" }))).toBeNull();
  });

  it("names the condition, with the mount for disks and no metric for an offline host", () => {
    expect(conditionText(alert({ id: "g", metric: "system.disk.utilization", labels: { mount: "C:" } }))).toBe("Disk utilisation (C:)");
    expect(conditionText(alert({ id: "h", rule: "host_offline", metric: null }))).toBe("Host stopped reporting telemetry");
  });
});

describe("alertsCapped", () => {
  it("is true only when fewer rows were loaded than the server reports", () => {
    expect(alertsCapped(200, 201)).toBe(true);
    expect(alertsCapped(200, 200)).toBe(false);
  });
});
