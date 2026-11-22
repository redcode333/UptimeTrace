import { ApiError } from "../../api/client";

/** A failed start or stop, in words. The server message is used when it is specific; nothing about the request is echoed. */
export function rehearsalFailure(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.isNetwork) return "Could not reach the UptimeTrace API. Nothing was started.";
    switch (e.code) {
      case "host_not_eligible":
        return "That host is not a synthetic host, so a rehearsal cannot target it. Nothing was started.";
      case "host_deactivated":
        return "That host is deactivated, so it cannot be targeted. Nothing was started.";
      case "host_not_registered":
        return "That host is no longer registered. Nothing was started.";
      case "rehearsal_already_running":
        return "A rehearsal is already running. Wait for it to finish or stop it first.";
      case "scenario_cannot_cross_threshold":
        return e.message;
      case "rehearsal_not_found":
        return "That rehearsal no longer exists.";
      default:
        return e.message || "The request failed. Nothing was changed.";
    }
  }
  return "The request failed. Nothing was changed.";
}
