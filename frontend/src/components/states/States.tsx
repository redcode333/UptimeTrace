import type { ReactNode } from "react";
import { ApiError } from "../../api/client";

/** Shared empty / loading / error / not-available blocks (approved States board). Plain language, no fake data. */

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="state" role="status" aria-busy="true">
      <div className="skeleton" style={{ width: "40%" }} />
      <div className="skeleton" />
      <div className="skeleton" style={{ width: "80%" }} />
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function errorCopy(error: unknown): { title: string; body: string } {
  if (error instanceof ApiError) {
    if (error.isNetwork) return { title: "Could not reach the API", body: "The console could not reach the UptimeTrace API (network error). Check that the backend is running, then retry." };
    if (error.status === 403) return { title: "No access", body: "Your account is signed in but is not authorized to view this workspace." };
    if (error.status === 503) return { title: "Temporarily unavailable", body: error.message || "The service is temporarily unavailable. Retry shortly." };
    if (error.status === 404) return { title: "Not found", body: error.message || "That item does not exist." };
    if (error.status === 422) return { title: "Invalid request", body: error.details[0]?.message ?? error.message };
  }
  return { title: "Something went wrong", body: "The request failed. Retry, and if it keeps failing check the backend logs." };
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { title, body } = errorCopy(error);
  return (
    <div className="state" role="alert">
      <span className="flag crit">REQUEST FAILED</span>
      <strong>{title}</strong>
      <p>{body}</p>
      {onRetry && <button type="button" className="btn" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function NotAvailable({ title = "Not available yet", reason, children }: { title?: string; reason: string; children?: ReactNode }) {
  return (
    <div className="state">
      <strong>{title}</strong>
      <p>{reason}</p>
      {children}
    </div>
  );
}

export function EmptyState({ title, body, children }: { title: string; body: string; children?: ReactNode }) {
  return (
    <div className="state">
      <strong>{title}</strong>
      <p>{body}</p>
      {children}
    </div>
  );
}
