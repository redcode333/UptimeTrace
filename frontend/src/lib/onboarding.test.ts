import { describe, expect, it } from "vitest";
import type { RegistryHost } from "../api/types";
import { deriveStep, installCommands, validateRegistration, verifyState } from "./onboarding";

const reg = (over: Partial<RegistryHost> & { host_id: string }): RegistryHost => ({
  kind: "live", display_name: null, created_at: "2026-11-19T10:00:00Z", created_by: "user_1", deactivated_at: null,
  telemetry_state: "awaiting_first_telemetry", last_envelope_at: null, active_credentials: 0, ...over,
});

describe("validateRegistration: host id (mirrors the backend HOST_ID pattern)", () => {
  const base = { hostId: "web-01", kind: "live" as const, displayName: "" };

  it("accepts ids of letters, digits, dot, underscore, colon and hyphen, starting alphanumeric", () => {
    expect(validateRegistration(base).valid).toBe(true);
    expect(validateRegistration({ ...base, hostId: "a.b_c:d-9" }).valid).toBe(true);
  });

  it("accepts the 128-character maximum and rejects 129", () => {
    expect(validateRegistration({ ...base, hostId: "a".repeat(128) }).valid).toBe(true);
    expect(validateRegistration({ ...base, hostId: "a".repeat(129) }).errors.hostId).toBeDefined();
  });

  it("rejects an empty id, a leading hyphen, spaces and slashes", () => {
    expect(validateRegistration({ ...base, hostId: "" }).errors.hostId).toMatch(/Enter a host id/);
    expect(validateRegistration({ ...base, hostId: "-web" }).errors.hostId).toBeDefined();
    expect(validateRegistration({ ...base, hostId: "web 01" }).errors.hostId).toBeDefined();
    expect(validateRegistration({ ...base, hostId: "web/01" }).errors.hostId).toBeDefined();
  });

  it("ignores surrounding whitespace in the id", () => {
    expect(validateRegistration({ ...base, hostId: "  web-01  " }).valid).toBe(true);
  });
});

describe("validateRegistration: kind matches the id prefix, as the server requires", () => {
  it("refuses a live host whose id is reserved for simulator or replay hosts", () => {
    expect(validateRegistration({ hostId: "sim01-web", kind: "live", displayName: "" }).errors.kind).toMatch(/reserved/);
    expect(validateRegistration({ hostId: "replay-x", kind: "live", displayName: "" }).errors.kind).toMatch(/reserved/);
  });

  it("requires a synthetic id to start with sim, and a replay id to start with replay-", () => {
    expect(validateRegistration({ hostId: "web-01", kind: "synthetic", displayName: "" }).errors.kind).toMatch(/must start with sim/);
    expect(validateRegistration({ hostId: "sim01-web", kind: "synthetic", displayName: "" }).valid).toBe(true);
    expect(validateRegistration({ hostId: "web-01", kind: "replay", displayName: "" }).errors.kind).toMatch(/replay-/);
    expect(validateRegistration({ hostId: "replay-a", kind: "replay", displayName: "" }).valid).toBe(true);
  });

  it("limits the display name to 253 characters", () => {
    expect(validateRegistration({ hostId: "web-01", kind: "live", displayName: "x".repeat(254) }).errors.displayName).toBeDefined();
  });
});

describe("deriveStep: the step comes from server state only", () => {
  it("asks for registration when no host is selected or registered", () => {
    expect(deriveStep(undefined, undefined)).toBe("register");
  });

  it("asks for a credential when the host has none", () => {
    expect(deriveStep(reg({ host_id: "web-01" }), 0)).toBe("credential");
  });

  it("moves to install once a credential exists but no telemetry has arrived", () => {
    expect(deriveStep(reg({ host_id: "web-01" }), 1)).toBe("install");
  });

  it("is connected only when a real envelope has been received, not when the host is merely registered", () => {
    expect(deriveStep(reg({ host_id: "web-01", active_credentials: 1 }), 1)).toBe("install");
    expect(deriveStep(reg({ host_id: "web-01", last_envelope_at: "2026-11-19T10:05:00Z", telemetry_state: "reporting" }), 1)).toBe("connected");
  });

  it("returns a host that reported once and is now stale or silent to verification, not to connected", () => {
    const at = "2026-11-19T10:05:00Z";
    expect(deriveStep(reg({ host_id: "web-01", last_envelope_at: at, telemetry_state: "stale" }), 1)).toBe("verify");
    expect(deriveStep(reg({ host_id: "web-01", last_envelope_at: at, telemetry_state: "silent" }), 1)).toBe("verify");
  });
});

describe("verifyState: registration is never reporting", () => {
  it("is not registered when the host is missing from the registry", () => {
    expect(verifyState(undefined)).toBe("not_registered");
  });

  it("is awaiting first telemetry for a host that has never sent an envelope, even if its state says otherwise", () => {
    expect(verifyState(reg({ host_id: "a" }))).toBe("awaiting_first_telemetry");
    expect(verifyState(reg({ host_id: "a", telemetry_state: "reporting", last_envelope_at: null }))).toBe("awaiting_first_telemetry");
  });

  it("is reporting, stale, silent or replay from the server's telemetry state", () => {
    const at = "2026-11-19T10:05:00Z";
    expect(verifyState(reg({ host_id: "a", last_envelope_at: at, telemetry_state: "reporting" }))).toBe("reporting");
    expect(verifyState(reg({ host_id: "a", last_envelope_at: at, telemetry_state: "stale" }))).toBe("stale");
    expect(verifyState(reg({ host_id: "a", last_envelope_at: at, telemetry_state: "silent" }))).toBe("silent");
    expect(verifyState(reg({ host_id: "a", last_envelope_at: at, telemetry_state: "replay" }))).toBe("replay");
  });
});

describe("installCommands: placeholders only, never a token", () => {
  const cmds = installCommands({ url: "https://api.example", hostId: "web-01", agentId: "agent-web-01" });

  it("builds the agent from this repository, with the command the end-to-end script uses", () => {
    expect(cmds.build).toContain("go build -o uptimetrace-agent ./cmd/agent");
  });

  it("configures the agent with environment variables and a token placeholder", () => {
    expect(cmds.unix).toContain("UPTIMETRACE_URL=https://api.example");
    expect(cmds.unix).toContain("UPTIMETRACE_HOST_ID=web-01");
    expect(cmds.unix).toContain("UPTIMETRACE_AGENT_ID=agent-web-01");
    expect(cmds.unix).toContain("UPTIMETRACE_TOKEN=<token>");
    expect(cmds.windows).toContain('$env:UPTIMETRACE_TOKEN = "<token>"');
  });

  it("contains no raw credential value", () => {
    expect(`${cmds.build}${cmds.unix}${cmds.windows}`).not.toMatch(/utk_|utm_/);
  });
});
