import { describe, expect, it } from "vitest";
import type { Alert, Host, Source } from "../api/types";
import { alertsInScope, indexHosts } from "./commandCenter";
import { host } from "../test/fixtures";

const alert = (id: string, hostId: string): Alert => ({
  id, host_id: hostId, condition_key: id, rule: "threshold", metric: null, labels: {}, severity: "critical", state: "open",
  value: null, threshold: null, opened_at: "2026-11-20T10:00:00Z", last_evaluated_at: "2026-11-20T10:00:00Z",
  acknowledged_at: null, acknowledged_by: null, resolved_at: null, resolved_by: null, resolution: null,
});

const hosts: Host[] = [
  host({ host_id: "live-1", source: "live" }),
  host({ host_id: "sim-1", source: "synthetic" }),
];
const index = indexHosts(hosts);
const ALL: Source[] = ["live", "synthetic", "replay"];

describe("alertsInScope", () => {
  const alerts = [alert("a", "live-1"), alert("b", "sim-1"), alert("c", "unknown-host")];

  it("keeps every alert when all sources are selected, including alerts whose host is not loaded", () => {
    const r = alertsInScope(alerts, ALL, index);
    expect(r.alerts).toHaveLength(3);
    expect(r.notLoaded).toBe(0);
  });

  it("keeps only alerts from the selected sources when the scope is narrowed", () => {
    const r = alertsInScope(alerts, ["live"], index);
    expect(r.alerts.map((a) => a.id)).toEqual(["a"]);
  });

  it("counts alerts whose host is not loaded instead of placing them in or out of scope", () => {
    const r = alertsInScope(alerts, ["live"], index);
    expect(r.notLoaded).toBe(1);
  });

  it("does not guess when no host index is available", () => {
    const r = alertsInScope(alerts, ["live"], null);
    expect(r.alerts).toHaveLength(0);
    expect(r.notLoaded).toBe(3);
  });
});
