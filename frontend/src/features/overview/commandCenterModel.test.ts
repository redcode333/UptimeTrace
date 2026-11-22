import { describe, expect, it } from "vitest";
import type { Alert } from "../../api/types";
import { host } from "../../test/fixtures";
import { bucketMsFor, hexLayout, insightsFrom, toneOf } from "./commandCenterModel";


function alert(over: Partial<Alert> & { id: string; opened_at: string }): Alert {
  return {
    host_id: "h1", condition_key: "k", rule: "threshold", metric: "system.cpu.utilization", labels: {}, severity: "warning",
    state: "open", value: 97, threshold: 95, last_evaluated_at: over.opened_at, acknowledged_at: null, acknowledged_by: null,
    resolved_at: null, resolved_by: null, resolution: null, ...over,
  } as Alert;
}

describe("toneOf", () => {
  it("maps each reported health state to a design tone and keeps stale and unknown neutral", () => {
    expect(toneOf("healthy")).toBe("normal");
    expect(toneOf("warning")).toBe("warning");
    expect(toneOf("critical")).toBe("critical");
    expect(toneOf("offline")).toBe("offline");
    expect(toneOf("stale")).toBe("neutral");
    expect(toneOf("unknown")).toBe("neutral");
  });
});

describe("hexLayout", () => {
  it("lays hosts out sorted by hostname and carries the reported CPU, or null when none was reported", () => {
    const cells = hexLayout([host({ host_id: "b", hostname: "zeta", latest: { cpu_percent: 40 } as never }), host({ host_id: "a", hostname: "alpha", latest: { cpu_percent: null } as never })], 4);
    expect(cells.map((c) => c.host.hostname)).toEqual(["alpha", "zeta"]);
    expect(cells[0]!.cpu).toBeNull();
    expect(cells[1]!.cpu).toBe(40);
  });
});

describe("bucketMsFor", () => {
  it("picks a bucket size that keeps every window readable", () => {
    expect(bucketMsFor(3_600_000)).toBe(5 * 60_000);
    expect(bucketMsFor(6 * 3_600_000)).toBe(15 * 60_000);
    expect(bucketMsFor(24 * 3_600_000)).toBe(30 * 60_000);
    expect(bucketMsFor(7 * 24 * 3_600_000)).toBe(180 * 60_000);
  });
});

describe("insightsFrom", () => {
  const hosts = [host({ host_id: "old-1", hostname: "live-lnx-03", reporting_status: "silent", last_seen_age_seconds: 840, last_seen: "2026-11-20T11:46:00Z" } as never)];
  const names = (id: string) => (id === "h1" ? "dev-win-01" : undefined);

  it("turns each open critical alert into a rule-fired card with the real host and values", () => {
    const cards = insightsFrom([alert({ id: "a1", host_id: "h1", opened_at: "2026-11-20T10:00:00Z", severity: "critical", value: 98, threshold: 95 })], [], names);
    const fired = cards.find((c) => c.id === "alert-a1")!;
    expect(fired.title).toBe("Rule fired · CPU utilization");
    expect(fired.body).toContain("dev-win-01");
    expect(fired.body).toContain("98 against 95");
  });

  it("ignores warnings and resolved critical alerts", () => {
    const cards = insightsFrom([
      alert({ id: "w", severity: "warning", opened_at: "2026-11-20T10:00:00Z" }),
      alert({ id: "r", severity: "critical", state: "resolved", opened_at: "2026-11-20T10:00:00Z" }),
    ], [], names);
    expect(cards.some((c) => c.id.startsWith("alert-"))).toBe(false);
  });

  it("reports a silent agent as no report, from its recorded last-seen time", () => {
    const cards = insightsFrom([], hosts, names);
    expect(cards.find((c) => c.id === "silent-old-1")!.body).toContain("14 minutes");
  });

  it("always states the outlook is not available and never invents a score", () => {
    const outlook = insightsFrom([], [], names).find((c) => c.id === "outlook")!;
    expect(outlook.title).toMatch(/not available/);
    expect(outlook.body).not.toMatch(/\d\.\d/);
  });
});
