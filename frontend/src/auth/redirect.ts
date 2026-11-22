/**
 * Where a user goes after signing in. Only same-origin paths are accepted: anything else (absolute URLs, protocol-relative
 * "//host" paths, backslash tricks, the auth pages themselves) falls back to the console home. This stops the sign-in page
 * being used to send people to another site.
 */
export function safeReturnPath(raw: string | null | undefined, fallback = "/"): string {
  if (!raw || typeof raw !== "string") return fallback;
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return fallback;
  if (raw.startsWith("/sign-in") || raw.startsWith("/sign-up")) return fallback;
  return raw;
}

/** The sign-in URL that carries the page the visitor asked for. */
export function signInUrlFor(path: string): string {
  return `/sign-in?redirect_url=${encodeURIComponent(path)}`;
}
