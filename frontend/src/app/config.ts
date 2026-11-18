/** Build-time configuration. Only public values live here: the browser must never hold a secret. */
export const clerkPublishableKey: string | undefined = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || undefined;
/** Empty string = same origin (the Vite dev proxy forwards /api to the backend). */
export const apiBaseUrl: string = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");
