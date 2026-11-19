import { describe, expect, it } from "vitest";
import { activeCount, authorizationHeader, tokenStatus, validateTokenDraft, type TokenDraft } from "./mcpTokens";

const draft = (over: Partial<TokenDraft> = {}): TokenDraft => ({ name: "laptop-claude", scopes: ["hosts:read"], days: "30", ...over });

describe("validateTokenDraft: name", () => {
  it("accepts a name at the 64-character limit", () => {
    expect(validateTokenDraft(draft({ name: "a".repeat(64) })).valid).toBe(true);
  });

  it("rejects a name of 65 characters", () => {
    expect(validateTokenDraft(draft({ name: "a".repeat(65) })).errors.name).toMatch(/1 to 64/);
  });

  it("requires a letter or digit first, and allows dots, underscores and hyphens after it", () => {
    expect(validateTokenDraft(draft({ name: "-leading" })).errors.name).toBeDefined();
    expect(validateTokenDraft(draft({ name: ".hidden" })).errors.name).toBeDefined();
    expect(validateTokenDraft(draft({ name: "a.b_c-d9" })).valid).toBe(true);
  });

  it("rejects spaces and other characters", () => {
    expect(validateTokenDraft(draft({ name: "my token" })).errors.name).toBeDefined();
    expect(validateTokenDraft(draft({ name: "tok/en" })).errors.name).toBeDefined();
  });

  it("requires a name", () => {
    expect(validateTokenDraft(draft({ name: "   " })).errors.name).toMatch(/Give the token a name/);
  });
});

describe("validateTokenDraft: scopes and expiry", () => {
  it("requires at least one scope", () => {
    expect(validateTokenDraft(draft({ scopes: [] })).errors.scopes).toBe("Choose at least one scope.");
  });

  it("accepts expiry from 1 to 90 whole days and rejects the rest", () => {
    expect(validateTokenDraft(draft({ days: "1" })).valid).toBe(true);
    expect(validateTokenDraft(draft({ days: "90" })).valid).toBe(true);
    expect(validateTokenDraft(draft({ days: "0" })).errors.days).toBeDefined();
    expect(validateTokenDraft(draft({ days: "91" })).errors.days).toBeDefined();
    expect(validateTokenDraft(draft({ days: "7.5" })).errors.days).toBeDefined();
    expect(validateTokenDraft(draft({ days: "" })).errors.days).toBeDefined();
  });
});

describe("tokenStatus", () => {
  const now = Date.parse("2026-11-19T12:00:00Z");

  it("is active before expiry and not revoked", () => {
    expect(tokenStatus({ revoked_at: null, expires_at: "2026-12-01T00:00:00Z" }, now)).toBe("active");
  });

  it("is expired at the expiry instant, matching the server's rule (expires_at <= now)", () => {
    expect(tokenStatus({ revoked_at: null, expires_at: "2026-11-19T12:00:00Z" }, now)).toBe("expired");
  });

  it("is revoked, and revocation takes precedence over expiry", () => {
    expect(tokenStatus({ revoked_at: "2026-11-19T11:00:00Z", expires_at: "2026-11-19T11:30:00Z" }, now)).toBe("revoked");
  });

  it("counts only active tokens", () => {
    const tokens = [
      { revoked_at: null, expires_at: "2026-12-01T00:00:00Z" },
      { revoked_at: "2026-11-19T11:00:00Z", expires_at: "2026-12-01T00:00:00Z" },
      { revoked_at: null, expires_at: "2026-11-01T00:00:00Z" },
    ];
    expect(activeCount(tokens, now)).toBe(1);
  });
});

describe("authorizationHeader", () => {
  it("builds the bearer header value from the token", () => {
    expect(authorizationHeader("utm_abc")).toBe("Bearer utm_abc");
  });
});
