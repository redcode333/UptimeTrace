import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { Credential, IssuedCredential, RegistryHost } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { AgentsPage } from "./AgentsPage";

const TOKEN = "utk_ONLY_SHOWN_ONCE_0123456789abcdefghijklmnop";

const reg = (over: Partial<RegistryHost> & { host_id: string }): RegistryHost => ({
  kind: "live", display_name: null, created_at: "2026-11-18T10:00:00Z", created_by: "user_1", deactivated_at: null,
  telemetry_state: "reporting", last_envelope_at: "2026-11-18T10:05:00Z", active_credentials: 1, ...over,
});
const cred = (over: Partial<Credential> & { id: string; name: string }): Credential => ({
  agent_id: "agent-web-01", created_at: "2026-11-18T10:00:00Z", created_by: "user_1", revoked_at: null, revoked_by: null, ...over,
});

describe("Agents page (host registry)", () => {
  it("shows an honest empty state when nothing is registered", async () => {
    renderApp(<AgentsPage />, { api: fakeApi({ registryHosts: async () => [] }) });
    expect(await screen.findByText("No hosts registered yet")).toBeInTheDocument();
  });

  it("lists registered hosts with their real telemetry state; a never-reported host is not shown as healthy", async () => {
    renderApp(<AgentsPage />, {
      api: fakeApi({
        registryHosts: async () => [
          reg({ host_id: "web-01", display_name: "Web 01" }),
          reg({ host_id: "new-01", telemetry_state: "awaiting_first_telemetry", last_envelope_at: null, active_credentials: 0 }),
          reg({ host_id: "old-01", telemetry_state: "stale" }),
          reg({ host_id: "gone-01", telemetry_state: "silent" }),
        ],
      }),
    });
    await screen.findByText("web-01");
    expect(screen.getByText("Awaiting first telemetry")).toBeInTheDocument();
    expect(screen.getByText("Stale")).toBeInTheDocument();
    expect(screen.getByText("Silent (offline)")).toBeInTheDocument();
    const row = screen.getByText("new-01").closest("tr")!;
    expect(within(row).getByText("—")).toBeInTheDocument();           // no last telemetry: a dash, never a fake time
    expect(within(row).queryByText(/healthy/i)).toBeNull();
  });

  it("registers a host with the typed values and shows a useful error when the API refuses", async () => {
    const registerHost = vi.fn()
      .mockRejectedValueOnce(new ApiError(409, "host_exists", "A host with that id is already registered."))
      .mockResolvedValueOnce(reg({ host_id: "web-02", telemetry_state: "awaiting_first_telemetry", last_envelope_at: null, active_credentials: 0 }));
    renderApp(<AgentsPage />, { api: fakeApi({ registryHosts: async () => [], registerHost, credentials: async () => [] }) });
    await screen.findByText("No hosts registered yet");
    await userEvent.type(screen.getByLabelText("Host id"), "web-02");
    await userEvent.type(screen.getByLabelText("Display name"), "Second web");
    await userEvent.click(screen.getByRole("button", { name: "Register host" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already registered");
    expect(registerHost).toHaveBeenCalledWith({ host_id: "web-02", kind: "live", display_name: "Second web" });
    await userEvent.click(screen.getByRole("button", { name: "Register host" }));
    await waitFor(() => expect(registerHost).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText(/already registered/)).toBeNull());
  });

  it("issues a credential, shows the token once with a warning, and never again after it is dismissed", async () => {
    let creds: Credential[] = [];
    const issueCredential = vi.fn(async () => {
      const c: IssuedCredential = { ...cred({ id: "c1", name: "cred-1" }), token: TOKEN };
      creds = [cred({ id: "c1", name: "cred-1" })];     // the listing never contains the token
      return c;
    });
    renderApp(<AgentsPage />, { api: fakeApi({ registryHosts: async () => [reg({ host_id: "web-01" })], credentials: async () => creds, issueCredential }) });
    await userEvent.click(await screen.findByRole("button", { name: "Credentials for web-01" }));
    await screen.findByText(/No credentials yet/);
    await userEvent.click(screen.getByRole("button", { name: "Issue credential" }));
    const box = await screen.findByRole("alert", { name: "New credential token" });
    expect(within(box).getByText(/shown only once/)).toBeInTheDocument();
    expect(within(box).getByTestId("issued-token")).toHaveTextContent(TOKEN);
    expect(issueCredential).toHaveBeenCalledWith("web-01", {});
    // the credential table lists the new credential's metadata, and the token is nowhere in it
    const row = (await screen.findByText("cred-1", { selector: "td" })).closest("table")!;
    expect(row.textContent).not.toContain(TOKEN);
    expect(within(row).getByText("ACTIVE")).toBeInTheDocument();
    await userEvent.click(within(box).getByRole("button", { name: /hide it/ }));
    expect(screen.queryByTestId("issued-token")).toBeNull();
    expect(document.body.textContent).not.toContain(TOKEN);
  });

  it("revocation needs an explicit confirmation; cancelling revokes nothing", async () => {
    const revokeCredential = vi.fn(async () => cred({ id: "c1", name: "web-01-agent", revoked_at: "2026-11-18T11:00:00Z", revoked_by: "user_1" }));
    renderApp(<AgentsPage />, {
      api: fakeApi({ registryHosts: async () => [reg({ host_id: "web-01" })], credentials: async () => [cred({ id: "c1", name: "web-01-agent" })], revokeCredential }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Credentials for web-01" }));
    await userEvent.click(await screen.findByRole("button", { name: "Revoke web-01-agent" }));
    expect(screen.getByText(/rejected on its next request/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(revokeCredential).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Revoke web-01-agent" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));
    await waitFor(() => expect(revokeCredential).toHaveBeenCalledWith("web-01", "c1"));
  });

  it("shows revoked credentials as revoked and offers no action on them", async () => {
    renderApp(<AgentsPage />, {
      api: fakeApi({
        registryHosts: async () => [reg({ host_id: "web-01", active_credentials: 0 })],
        credentials: async () => [cred({ id: "c1", name: "old-agent", revoked_at: "2026-11-18T11:00:00Z", revoked_by: "user_1" })],
      }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Credentials for web-01" }));
    expect(await screen.findByText("REVOKED")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke old-agent" })).toBeNull();
  });

  it("explains a failed revoke instead of silently doing nothing", async () => {
    const revokeCredential = vi.fn().mockRejectedValue(new ApiError(0, "network_error", "Could not reach the UptimeTrace API."));
    renderApp(<AgentsPage />, {
      api: fakeApi({ registryHosts: async () => [reg({ host_id: "web-01" })], credentials: async () => [cred({ id: "c1", name: "web-01-agent" })], revokeCredential }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Credentials for web-01" }));
    await userEvent.click(await screen.findByRole("button", { name: "Revoke web-01-agent" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));
    expect(await screen.findByText(/Could not revoke the credential\. Could not reach the UptimeTrace API\. Nothing was changed\./)).toBeInTheDocument();
  });

  it("shows a request error with a retry when the registry cannot be loaded", async () => {
    renderApp(<AgentsPage />, { api: fakeApi({ registryHosts: async () => { throw new ApiError(503, "unavailable", "Temporarily unavailable."); } }) });
    expect(await screen.findByText("Temporarily unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
