/**
 * One vocabulary for whether a host's agent is connected, used by Agents, Hosts and Host detail.
 *
 * The backend states come from telemetry and freshness rules, never from registration:
 *   awaiting_first_telemetry -> pending    (registered, no envelope yet)
 *   reporting                -> connected  (an envelope arrived recently)
 *   stale                    -> stale      (late; last values are not judged)
 *   silent                   -> offline    (no telemetry for longer than the offline timeout; the alert system calls this offline)
 *   replay                   -> replay     (history, not a live connection)
 */
export type ConnectionState = "pending" | "connected" | "stale" | "offline" | "replay";

export const CONNECTION_LABEL: Record<ConnectionState, string> = {
  pending: "Pending",
  connected: "Connected",
  stale: "Stale",
  offline: "Offline",
  replay: "Replay (not live)",
};

const FROM_BACKEND: Record<string, ConnectionState> = {
  awaiting_first_telemetry: "pending",
  reporting: "connected",
  stale: "stale",
  silent: "offline",
  replay: "replay",
};

/** Maps a backend telemetry or reporting state to the console vocabulary. Unknown values return null rather than guessing. */
export function connectionState(backend: string): ConnectionState | null {
  return FROM_BACKEND[backend] ?? null;
}
