/**
 * First-time onboarding: validation, state derivation and install commands. No persisted onboarding state exists on the server,
 * so every step is derived from the registry (hosts, telemetry state, credentials). Nothing here is stored in the browser.
 *
 * Validation mirrors backend/app/monitoring/router.py HOST_ID and backend/app/auth.py check_scope (kind <-> host-id prefix).
 */
import type { RegistryHost, RegistryHostKind } from "../api/types";

export const HOST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const SYNTHETIC_PREFIX = /^sim\d*-/;
const REPLAY_PREFIX = /^replay-/;
export const DISPLAY_NAME_MAX = 253;

export interface RegistrationDraft {
  hostId: string;
  kind: RegistryHostKind;
  displayName: string;
}

export interface RegistrationValidation {
  valid: boolean;
  errors: Partial<Record<"hostId" | "kind" | "displayName", string>>;
}

export function validateRegistration(d: RegistrationDraft): RegistrationValidation {
  const errors: RegistrationValidation["errors"] = {};
  const id = d.hostId.trim();
  if (id === "") errors.hostId = "Enter a host id. It identifies this machine in UptimeTrace and must match the agent's configuration.";
  else if (!HOST_ID_PATTERN.test(id)) errors.hostId = "Use up to 128 letters, digits, '.', '_', ':' or '-', starting with a letter or digit.";
  else if (d.kind === "live" && (SYNTHETIC_PREFIX.test(id) || REPLAY_PREFIX.test(id))) {
    errors.kind = "Ids starting with sim or replay- are reserved for synthetic and replay hosts. Choose another id for a real machine.";
  } else if (d.kind === "synthetic" && !SYNTHETIC_PREFIX.test(id)) {
    errors.kind = "A synthetic host id must start with sim, for example sim01-web.";
  } else if (d.kind === "replay" && !REPLAY_PREFIX.test(id)) {
    errors.kind = "A replay host id must start with replay-.";
  }
  if (d.displayName.trim().length > DISPLAY_NAME_MAX) errors.displayName = `At most ${DISPLAY_NAME_MAX} characters.`;
  return { valid: Object.keys(errors).length === 0, errors };
}

/** Where a first-time user is, derived from the registry and credential list. */
export type OnboardingStep = "register" | "credential" | "install" | "verify" | "connected";

export function deriveStep(host: RegistryHost | undefined, activeCredentials: number | undefined): OnboardingStep {
  if (!host) return "register";
  // Connected means reporting now. A host that reported once and is now late or silent is back in verification.
  if (host.last_envelope_at && host.telemetry_state === "reporting") return "connected";
  if (host.last_envelope_at) return "verify";
  if (activeCredentials === 0) return "credential";
  return "install";
}

/**
 * The verification state of a host. "Registered" is never treated as "reporting": only a real envelope counts.
 * The registry's telemetry state comes from the server (reporting, stale, silent, replay, awaiting_first_telemetry).
 */
export type VerifyState = "not_registered" | "awaiting_first_telemetry" | "reporting" | "stale" | "silent" | "replay";

export function verifyState(host: RegistryHost | undefined): VerifyState {
  if (!host) return "not_registered";
  if (!host.last_envelope_at || host.telemetry_state === "awaiting_first_telemetry") return "awaiting_first_telemetry";
  if (host.telemetry_state === "reporting") return "reporting";
  if (host.telemetry_state === "stale") return "stale";
  if (host.telemetry_state === "silent") return "silent";
  return "replay";
}

export interface InstallValues {
  url: string;
  hostId: string;
  agentId: string;
}

/** Commands for the manual install this repository supports: build from source, then run with environment configuration. */
export function installCommands(v: InstallValues) {
  const build = "cd agent\ngo build -o uptimetrace-agent ./cmd/agent";
  const unix = [
    `UPTIMETRACE_URL=${v.url} \\`,
    `UPTIMETRACE_HOST_ID=${v.hostId} \\`,
    `UPTIMETRACE_AGENT_ID=${v.agentId} \\`,
    "UPTIMETRACE_TOKEN=<token> \\",
    "./uptimetrace-agent",
  ].join("\n");
  const windows = [
    `$env:UPTIMETRACE_URL = "${v.url}"`,
    `$env:UPTIMETRACE_HOST_ID = "${v.hostId}"`,
    `$env:UPTIMETRACE_AGENT_ID = "${v.agentId}"`,
    '$env:UPTIMETRACE_TOKEN = "<token>"',
    ".\\uptimetrace-agent.exe",
  ].join("\n");
  return { build, unix, windows };
}

/** Telemetry is collected every 60 seconds by default; the agent's interval can be changed with UPTIMETRACE_INTERVAL. */
export const DEFAULT_INTERVAL_SECONDS = 60;
