/**
 * MCP access tokens: form validation, scope descriptions and status. Limits are copied from the backend
 * (backend/app/auth.py NAME; backend/app/mcp/tokens.py SCOPES, DEFAULT_TTL_DAYS, MAX_TTL_DAYS, MAX_ACTIVE_PER_WORKSPACE).
 * The server remains the authority; these checks only stop an invalid request from being sent.
 */
export const MCP_SCOPES = ["hosts:read", "metrics:read"] as const;
export type McpScope = (typeof MCP_SCOPES)[number];

export const SCOPE_DESCRIPTION: Record<McpScope, { title: string; tools: string }> = {
  "hosts:read": { title: "Read hosts and health", tools: "list_hosts, get_host_health" },
  "metrics:read": { title: "Read host metrics", tools: "get_host_metrics" },
};

export const TTL_DEFAULT_DAYS = 30;
export const TTL_MAX_DAYS = 90;
export const MAX_ACTIVE_TOKENS = 20;
export const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

export interface TokenDraft {
  name: string;
  scopes: McpScope[];
  days: string;
}

export interface TokenValidation {
  valid: boolean;
  errors: Partial<Record<"name" | "scopes" | "days", string>>;
}

export function validateTokenDraft(d: TokenDraft): TokenValidation {
  const errors: TokenValidation["errors"] = {};
  if (d.name.trim() === "") errors.name = "Give the token a name, so you can recognise it later.";
  else if (!NAME_PATTERN.test(d.name)) errors.name = "Use 1 to 64 letters, digits, '.', '_' or '-'. It must start with a letter or digit.";
  if (d.scopes.length === 0) errors.scopes = "Choose at least one scope.";
  const text = d.days.trim();
  const days = text === "" ? NaN : Number(text);
  if (!Number.isInteger(days) || days < 1 || days > TTL_MAX_DAYS) {
    errors.days = `Whole days from 1 to ${TTL_MAX_DAYS}.`;
  }
  return { valid: Object.keys(errors).length === 0, errors };
}

export type TokenStatus = "active" | "expired" | "revoked";

export interface TokenLike {
  revoked_at: string | null;
  expires_at: string;
}

/** Revoked takes precedence over expired. Times come from the server; the comparison uses the caller's clock. */
export function tokenStatus(t: TokenLike, nowMs: number): TokenStatus {
  if (t.revoked_at) return "revoked";
  if (Date.parse(t.expires_at) <= nowMs) return "expired";
  return "active";
}

export function activeCount(tokens: readonly TokenLike[], nowMs: number): number {
  return tokens.filter((t) => tokenStatus(t, nowMs) === "active").length;
}

/** The bearer header value. Built only from the token shown once; never stored in the URL or in logs. */
export function authorizationHeader(token: string): string {
  return `Bearer ${token}`;
}
