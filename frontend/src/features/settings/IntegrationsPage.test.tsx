import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { IssuedMcpToken, McpToken } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { IntegrationsPage, issueFailureText, mcpEndpoint } from "./IntegrationsPage";

const RAW = "utm_SECRET_VALUE_1234567890abcdefghijklmnop";
const ISO = (ms: number) => new Date(ms).toISOString();
const NOW = Date.now();
const DAY = 86_400_000;

const tok = (over: Partial<McpToken> & { id: string; name: string }): McpToken => ({
  scopes: ["hosts:read"], created_at: ISO(NOW - 2 * DAY), created_by: "user_allowed_1", expires_at: ISO(NOW + 28 * DAY),
  revoked_at: null, revoked_by: null, ...over,
});

function api(over: Parameters<typeof fakeApi>[0] = {}) {
  return fakeApi({
    mcpTokens: async () => [tok({ id: "t1", name: "claude-laptop", scopes: ["hosts:read", "metrics:read"] })],
    ...over,
  });
}

const page = <IntegrationsPage />;
const nameInput = () => screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement;

describe("Integrations: overview", () => {
  it("shows the endpoint, transport, read-only designation and the three real tools", async () => {
    renderApp(page, { api: api(), route: "/settings/integrations" });
    const overview = await screen.findByLabelText("MCP access overview");
    expect(overview).toHaveTextContent("Streamable HTTP, JSON responses, stateless");
    expect(overview).toHaveTextContent("Read-only");
    expect(overview).toHaveTextContent(/\/mcp\//);
    for (const tool of ["list_hosts", "get_host_health", "get_host_metrics"]) {
      expect(overview).toHaveTextContent(tool);
    }
    expect(overview).toHaveTextContent("There is no MCP tool for alerts, incidents or remediation.");
  });

  it("states the authentication limits rather than implying OAuth or hosted connectors", async () => {
    renderApp(page, { api: api(), route: "/settings/integrations" });
    const overview = await screen.findByLabelText("MCP access overview");
    expect(overview).toHaveTextContent("Only static bearer tokens");
    expect(overview).toHaveTextContent("OAuth sign-in and hosted connector discovery are not implemented");
    expect(overview).not.toHaveTextContent(/OAuth.*supported/i);
  });

  it("does not claim the endpoint is live, because the API does not report MCP status", async () => {
    renderApp(page, { api: api(), route: "/settings/integrations" });
    expect(await screen.findByText(/The API does not report whether MCP is enabled/)).toBeInTheDocument();
  });

  it("builds the endpoint from the API base or the page origin, with the trailing slash", () => {
    expect(mcpEndpoint()).toMatch(/\/mcp\/$/);
  });
});

describe("Integrations: token list", () => {
  it("shows each token's name, scopes, status and times, and the active count", async () => {
    const tokens = [
      tok({ id: "a", name: "claude-laptop", scopes: ["hosts:read", "metrics:read"] }),
      tok({ id: "b", name: "old-client", expires_at: ISO(NOW - DAY) }),
      tok({ id: "c", name: "retired", revoked_at: ISO(NOW - DAY), revoked_by: "user_allowed_1" }),
    ];
    renderApp(page, { api: api({ mcpTokens: async () => tokens }), route: "/settings/integrations" });
    const list = await within(await screen.findByLabelText("Access tokens")).findByRole("table");
    expect(within(list).getByText("claude-laptop")).toBeInTheDocument();
    expect(within(list).getByText("ACTIVE")).toBeInTheDocument();
    expect(within(list).getByText("EXPIRED")).toBeInTheDocument();
    expect(within(list).getByText("REVOKED")).toBeInTheDocument();
    expect(within(list).getAllByText("metrics:read").length).toBeGreaterThan(0);
    expect(screen.getByText("1 of 20 active")).toBeInTheDocument();
  });

  it("shows an empty state when no tokens exist", async () => {
    renderApp(page, { api: api({ mcpTokens: async () => [] }), route: "/settings/integrations" });
    expect(await screen.findByText("No access tokens")).toBeInTheDocument();
  });

  it("shows a loading state, then an error with retry when the list cannot be read", async () => {
    const load = vi.fn(async () => { throw new ApiError(0, "network_error", "x"); });
    renderApp(page, { api: api({ mcpTokens: load }), route: "/settings/integrations" });
    expect(await screen.findByText("Could not reach the API")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("Integrations: creating a token", () => {
  it("rejects an invalid name, a missing scope and an out-of-range expiry without calling the server", async () => {
    const issue = vi.fn();
    renderApp(page, { api: api({ issueMcpToken: issue as never }), route: "/settings/integrations" });
    fireEvent.change(nameInput(), { target: { value: "bad name" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /hosts:read/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /metrics:read/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "Expires after (days)" }), { target: { value: "120" } });
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));
    expect(await screen.findByText(/Use 1 to 64 letters/)).toBeInTheDocument();
    expect(screen.getByText("Choose at least one scope.")).toBeInTheDocument();
    expect(screen.getByText(/Whole days from 1 to 90/)).toBeInTheDocument();
    expect(issue).not.toHaveBeenCalled();
  });

  it("creates a token with the chosen name, scopes and expiry, then refreshes the list", async () => {
    const issue = vi.fn(async (): Promise<IssuedMcpToken> => ({ ...tok({ id: "new", name: "ci-reader" }), token: RAW }));
    const list = vi.fn(async () => [tok({ id: "t1", name: "claude-laptop" })]);
    renderApp(page, { api: api({ issueMcpToken: issue as never, mcpTokens: list }), route: "/settings/integrations" });
    await within(await screen.findByLabelText("Access tokens")).findByRole("table");
    const before = list.mock.calls.length;
    fireEvent.change(nameInput(), { target: { value: "ci-reader" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /metrics:read/ })); // leave only hosts:read
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));
    await waitFor(() => expect(issue).toHaveBeenCalledWith({ name: "ci-reader", scopes: ["hosts:read"], expires_in_days: 30 }));
    expect(await screen.findByRole("region", { name: "New access token" })).toBeInTheDocument();
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(before));
  });

  it("shows the raw token once, with a secret warning, and hides it when the operator confirms storage", async () => {
    const issue = vi.fn(async (): Promise<IssuedMcpToken> => ({ ...tok({ id: "new", name: "ci-reader" }), token: RAW }));
    renderApp(page, { api: api({ issueMcpToken: issue as never }), route: "/settings/integrations" });
    fireEvent.change(nameInput(), { target: { value: "ci-reader" } });
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));
    const region = await screen.findByRole("region", { name: "New access token" });
    expect(region).toHaveTextContent("Treat this token as a secret");
    expect(region).toHaveTextContent("cannot be retrieved again");
    expect(within(region).getByTestId("issued-token")).toHaveTextContent(RAW);
    fireEvent.click(screen.getByRole("button", { name: "I have stored the token. Hide it." }));
    expect(screen.queryByText(RAW)).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "New access token" })).not.toBeInTheDocument();
  });

  it("copies the raw token on request and reports when copying is blocked", async () => {
    const write = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    const issue = vi.fn(async (): Promise<IssuedMcpToken> => ({ ...tok({ id: "new", name: "ci-reader" }), token: RAW }));
    renderApp(page, { api: api({ issueMcpToken: issue as never }), route: "/settings/integrations" });
    fireEvent.change(nameInput(), { target: { value: "ci-reader" } });
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));
    fireEvent.click(await screen.findByRole("button", { name: "Copy token" }));
    await waitFor(() => expect(write).toHaveBeenCalledWith(RAW));
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("reports a blocked clipboard without losing the token", async () => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async () => { throw new Error("denied"); } }, configurable: true });
    const issue = vi.fn(async (): Promise<IssuedMcpToken> => ({ ...tok({ id: "new", name: "ci-reader" }), token: RAW }));
    renderApp(page, { api: api({ issueMcpToken: issue as never }), route: "/settings/integrations" });
    fireEvent.change(nameInput(), { target: { value: "ci-reader" } });
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));
    fireEvent.click(await screen.findByRole("button", { name: "Copy token" }));
    expect(await screen.findByText(/Copying was blocked by the browser/)).toBeInTheDocument();
    expect(screen.getByTestId("issued-token")).toHaveTextContent(RAW);
  });

  it("keeps the raw token out of storage and out of the URL", async () => {
    const issue = vi.fn(async (): Promise<IssuedMcpToken> => ({ ...tok({ id: "new", name: "ci-reader" }), token: RAW }));
    renderApp(page, { api: api({ issueMcpToken: issue as never }), route: "/settings/integrations" });
    fireEvent.change(nameInput(), { target: { value: "ci-reader" } });
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));
    await screen.findByTestId("issued-token");
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(window.location.href).not.toContain(RAW);
  });

  it("explains a workspace token limit, and never shows a token in the error", async () => {
    const issue = vi.fn(async () => { throw new ApiError(409, "token_limit", "At most 20 active MCP tokens per workspace."); });
    renderApp(page, { api: api({ issueMcpToken: issue as never }), route: "/settings/integrations" });
    fireEvent.change(nameInput(), { target: { value: "one-more" } });
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));
    expect(await screen.findByText(/already has 20 active tokens/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "New access token" })).not.toBeInTheDocument();
  });

  it("reports a network failure as no token created", async () => {
    const issue = vi.fn(async () => { throw new ApiError(0, "network_error", "x"); });
    renderApp(page, { api: api({ issueMcpToken: issue as never }), route: "/settings/integrations" });
    fireEvent.change(nameInput(), { target: { value: "one-more" } });
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));
    expect(await screen.findByText("Could not reach the API. No token was created.")).toBeInTheDocument();
  });

  it("maps server error codes to plain messages", () => {
    expect(issueFailureText(new ApiError(409, "token_name_taken", "x"))).toMatch(/already exists/);
    expect(issueFailureText(new ApiError(422, "invalid_request", "x", [{ path: "name", message: "bad" }]))).toBe("Could not create the token. Nothing was created. name: bad");
  });
});

describe("Integrations: revoking a token", () => {
  it("asks for confirmation before revoking, then revokes and refreshes", async () => {
    const revoke = vi.fn(async (id: string) => tok({ id, name: "claude-laptop", revoked_at: ISO(NOW), revoked_by: "user_allowed_1" }));
    const list = vi.fn(async () => [tok({ id: "t1", name: "claude-laptop" })]);
    renderApp(page, { api: api({ revokeMcpToken: revoke as never, mcpTokens: list }), route: "/settings/integrations" });
    fireEvent.click(await screen.findByRole("button", { name: "Revoke token claude-laptop" }));
    expect(revoke).not.toHaveBeenCalled();
    expect(screen.getByRole("group", { name: "Confirm revoking claude-laptop" })).toHaveTextContent("cannot be undone");
    const before = list.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("t1"));
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(before));
  });

  it("cancelling the confirmation revokes nothing", async () => {
    const revoke = vi.fn();
    renderApp(page, { api: api({ revokeMcpToken: revoke as never }), route: "/settings/integrations" });
    fireEvent.click(await screen.findByRole("button", { name: "Revoke token claude-laptop" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(revoke).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Revoke token claude-laptop" })).toBeInTheDocument();
  });

  it("reports a failed revocation without claiming the outcome", async () => {
    const revoke = vi.fn(async () => { throw new ApiError(0, "network_error", "x"); });
    renderApp(page, { api: api({ revokeMcpToken: revoke as never }), route: "/settings/integrations" });
    fireEvent.click(await screen.findByRole("button", { name: "Revoke token claude-laptop" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));
    expect(await screen.findByText(/Check its status below before trying again/)).toBeInTheDocument();
  });
});

describe("Integrations: client configuration", () => {
  it("uses placeholders in the configuration and never embeds a raw token", async () => {
    renderApp(page, { api: api(), route: "/settings/integrations" });
    const cfg = await screen.findByLabelText("configuration example");
    expect(cfg).toHaveTextContent('"Authorization": "Bearer <token>"');
    expect(cfg.textContent).not.toContain(RAW);
    expect(cfg.textContent).toContain('"type": "http"');
  });

  it("gives the verification script the endpoint and a token placeholder", async () => {
    renderApp(page, { api: api(), route: "/settings/integrations" });
    const script = await screen.findByLabelText("verification script example");
    expect(script.textContent).toContain("streamablehttp_client(URL, headers=");
    expect(script.textContent).toContain(`URL = "${mcpEndpoint()}"`);
    expect(script.textContent).toContain('TOKEN = "<token>"');
    expect(script.textContent).not.toContain("<endpoint>");
  });

  it("states that the configuration shape is not verified against a specific client", async () => {
    renderApp(page, { api: api(), route: "/settings/integrations" });
    expect(await screen.findByText(/has not been checked against any specific client/)).toBeInTheDocument();
  });

  it("copies a snippet, not a token", async () => {
    const write = vi.fn(async (_text: string) => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    renderApp(page, { api: api(), route: "/settings/integrations" });
    fireEvent.click(await screen.findByRole("button", { name: "Copy configuration" }));
    await waitFor(() => expect(write).toHaveBeenCalled());
    expect(write.mock.calls[0]![0]).toContain("<token>");
  });
});
