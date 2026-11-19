import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import { ApiError } from "../../api/client";
import type { Credential, IssuedCredential, MonitoringSettings, RegistryHost } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { OnboardingPage } from "./OnboardingPage";

const RAW = "utk_ONBOARDING_SECRET_0123456789abcdefghijklmnopqrstuvwxyz";
const at = (ms: number) => new Date(Date.now() - ms).toISOString();
const MIN = 60_000;

const reg = (over: Partial<RegistryHost> & { host_id: string }): RegistryHost => ({
  kind: "live", display_name: null, created_at: at(10 * MIN), created_by: "user_test_1", deactivated_at: null,
  telemetry_state: "awaiting_first_telemetry", last_envelope_at: null, active_credentials: 0, ...over,
});
const cred = (over: Partial<Credential> & { id: string }): Credential => ({
  name: "web-01-1", agent_id: "agent-web-01", created_at: at(5 * MIN), created_by: "user_test_1", revoked_at: null, revoked_by: null, ...over,
});
const SETTINGS: MonitoringSettings = {
  config: {
    freshness: { stale_after_seconds: 90, offline_after_seconds: 300 },
    rules: {
      "system.cpu.utilization": { warning: 85, critical: 95 },
      "system.memory.utilization": { warning: 85, critical: 95 },
      "system.disk.utilization": { warning: 90, critical: 95 },
    },
  },
  defaults: {
    freshness: { stale_after_seconds: 90, offline_after_seconds: 300 },
    rules: {
      "system.cpu.utilization": { warning: 85, critical: 95 },
      "system.memory.utilization": { warning: 85, critical: 95 },
      "system.disk.utilization": { warning: 90, critical: 95 },
    },
  },
  version: 0, updated_at: null, updated_by: null,
};

/** A stateful registry, like the server: registrations and credentials show up in later reads. */
function registry(initial: RegistryHost[] = [], creds: Credential[] = []) {
  const hosts = [...initial];
  const credentials = [...creds];
  const calls = { list: 0 };
  return {
    calls,
    hosts,
    api: {
      registryHosts: vi.fn(async () => { calls.list++; return hosts.map((h) => ({ ...h, active_credentials: credentials.filter((c) => !c.revoked_at).length })); }),
      registerHost: vi.fn(async (b: { host_id: string; kind: RegistryHost["kind"]; display_name?: string }) => {
        const row = reg({ host_id: b.host_id, kind: b.kind, display_name: b.display_name ?? null });
        hosts.push(row);
        return row;
      }),
      credentials: vi.fn(async () => credentials.slice()),
      issueCredential: vi.fn(async (): Promise<IssuedCredential> => {
        const c = cred({ id: `c${credentials.length + 1}`, name: "web-01-new" });
        credentials.push(c);
        return { ...c, token: RAW };
      }),
      revokeCredential: vi.fn(async (_h: string, id: string) => {
        const c = credentials.find((x) => x.id === id)!;
        c.revoked_at = at(0);
        return c;
      }),
    },
  };
}

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search}</div>;
}
const page = (
  <>
    <Routes><Route path="/onboarding" element={<OnboardingPage />} /><Route path="*" element={<div>OTHER PAGE</div>} /></Routes>
    <Where />
  </>
);

describe("Onboarding: entry", () => {
  it("an empty workspace starts at registration", async () => {
    const r = registry();
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding" });
    expect(await screen.findByRole("heading", { name: /1\. Register the host/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Register host" })).toBeInTheDocument();
  });

  it("a workspace that already has hosts is not put through setup again", async () => {
    const r = registry([reg({ host_id: "web-01", last_envelope_at: at(MIN), telemetry_state: "reporting" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding" });
    const section = await screen.findByLabelText("Workspace hosts");
    expect(section).toHaveTextContent("setup is not required");
    expect(within(section).getByRole("link", { name: "View hosts" })).toHaveAttribute("href", "/hosts");
    expect(screen.queryByRole("button", { name: "Register host" })).not.toBeInTheDocument();
  });

  it("an existing-host user can still register another host on purpose", async () => {
    const r = registry([reg({ host_id: "web-01" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding" });
    fireEvent.click(await screen.findByRole("button", { name: "Register another host" }));
    expect(await screen.findByRole("button", { name: "Register host" })).toBeInTheDocument();
  });

  it("a link to a host that is not registered says so, and offers to start again", async () => {
    const r = registry();
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=ghost-9" });
    expect(await screen.findByText("That host is not registered")).toBeInTheDocument();
  });

  it("shows loading and an error with retry when the registry cannot be read", async () => {
    const load = vi.fn(async () => { throw new ApiError(0, "network_error", "x"); });
    renderApp(page, { api: fakeApi({ registryHosts: load as never }), route: "/onboarding" });
    expect(await screen.findByText("Could not reach the API")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("Onboarding: registration", () => {
  it("explains what is required and that registration is not reporting", async () => {
    const r = registry();
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding" });
    expect(await screen.findByText(/Registering does not mean the host is reporting/)).toBeInTheDocument();
    expect(screen.getByText(/Environment and other metadata are not collected/)).toBeInTheDocument();
  });

  it("refuses an invalid id or a kind that does not match the id, without calling the server", async () => {
    const r = registry();
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding" });
    fireEvent.change(await screen.findByRole("textbox", { name: "Host id" }), { target: { value: "sim01-web" } });
    expect(await screen.findByText(/reserved for synthetic and replay hosts/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Register host" }));
    expect(r.api.registerHost).not.toHaveBeenCalled();
  });

  it("registers the host, then moves to the credential step, with the host id in the URL", async () => {
    const r = registry();
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding" });
    fireEvent.change(await screen.findByRole("textbox", { name: "Host id" }), { target: { value: "web-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Register host" }));
    await waitFor(() => expect(r.api.registerHost).toHaveBeenCalledWith({ host_id: "web-01", kind: "live" }));
    expect(await screen.findByRole("button", { name: "Create agent credential" })).toBeInTheDocument();
    expect(screen.getByTestId("where")).toHaveTextContent("/onboarding?host=web-01");
    expect(screen.getByRole("region", { name: "Registered host" })).toHaveTextContent("Not received yet");
  });

  it("shows a duplicate id as a clear conflict, not a generic failure", async () => {
    const r = registry([reg({ host_id: "web-01" })]);
    r.api.registerHost.mockImplementationOnce(async () => { throw new ApiError(409, "host_exists", "x"); });
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding" });
    fireEvent.click(await screen.findByRole("button", { name: "Register another host" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "Host id" }), { target: { value: "web-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Register host" }));
    expect(await screen.findByText(/already exists\. Choose another id/)).toBeInTheDocument();
  });
});

describe("Onboarding: credentials", () => {
  it("shows the secret once from component state, marks it as a secret, and never puts it in storage or the URL", async () => {
    const r = registry([reg({ host_id: "web-01" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
    fireEvent.click(await screen.findByRole("button", { name: "Create agent credential" }));
    const region = await screen.findByRole("region", { name: "New agent credential" });
    expect(region).toHaveTextContent("This credential is a secret");
    expect(within(region).getByTestId("issued-credential")).toHaveTextContent(RAW);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(window.location.href).not.toContain(RAW);
    fireEvent.click(screen.getByRole("button", { name: "I have stored the credential. Hide it." }));
    expect(screen.queryByText(RAW)).not.toBeInTheDocument();
  });

  it("does not pretend an existing secret can be retrieved, and offers revoke and replace", async () => {
    const r = registry([reg({ host_id: "web-01" })], [cred({ id: "c1" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
    expect(await screen.findByText(/cannot be retrieved/)).toBeInTheDocument();
    expect(screen.queryByTestId("issued-credential")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(r.api.revokeCredential).toHaveBeenCalledWith("web-01", "c1"));
  });

  it("copies the credential on request, and reports a blocked clipboard", async () => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async () => { throw new Error("denied"); } }, configurable: true });
    const r = registry([reg({ host_id: "web-01" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
    fireEvent.click(await screen.findByRole("button", { name: "Create agent credential" }));
    fireEvent.click(await screen.findByRole("button", { name: "Copy credential" }));
    expect(await screen.findByText(/Copying was blocked by the browser/)).toBeInTheDocument();
  });
});

describe("Onboarding: installation instructions", () => {
  it("renders the build and run commands with the host and agent ids, and a token placeholder", async () => {
    const r = registry([reg({ host_id: "web-01" })], [cred({ id: "c1" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
    const build = await screen.findByLabelText("build command");
    expect(build).toHaveTextContent("go build -o uptimetrace-agent ./cmd/agent");
    expect(screen.getByLabelText("shell command")).toHaveTextContent("UPTIMETRACE_HOST_ID=web-01");
    expect(screen.getByLabelText("shell command")).toHaveTextContent("UPTIMETRACE_AGENT_ID=agent-web-01");
    expect(screen.getByLabelText("shell command")).toHaveTextContent("UPTIMETRACE_TOKEN=<token>");
    expect(screen.getByText(/There is no installer/)).toBeInTheDocument();
    expect(screen.getByText(/Linux collection exists but has not been run on a real Linux host/)).toBeInTheDocument();
  });

  it("never renders a credential value in the instructions", async () => {
    const r = registry([reg({ host_id: "web-01" })], [cred({ id: "c1" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
    await screen.findByLabelText("shell command");
    expect(document.body.textContent).not.toContain(RAW);
  });
});

describe("Onboarding: first telemetry verification", () => {
  it("a registered host with no telemetry is explicitly not yet reporting, with troubleshooting", async () => {
    const r = registry([reg({ host_id: "web-01" })], [cred({ id: "c1" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
    const verify = await screen.findByLabelText("Verify first telemetry");
    expect(await within(verify).findByText(/Waiting for the agent's first telemetry/)).toBeInTheDocument();
    expect(verify).toHaveTextContent("Registration alone does not mean the agent is reporting");
    expect(verify).toHaveTextContent("UPTIMETRACE_TOKEN");
    expect(screen.queryByText("First telemetry received")).not.toBeInTheDocument();
  });

  it("stale telemetry is described as late, not as a crash", async () => {
    const r = registry([reg({ host_id: "web-01", last_envelope_at: at(3 * MIN), telemetry_state: "stale" })], [cred({ id: "c1" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
    const verify = await screen.findByLabelText("Verify first telemetry");
    expect(await within(verify).findByText(/Telemetry is late/)).toBeInTheDocument();
    expect(verify).toHaveTextContent("This is not a confirmed failure yet");
  });

  it("silent telemetry is described as offline, with the cause left open", async () => {
    const r = registry([reg({ host_id: "web-01", last_envelope_at: at(20 * MIN), telemetry_state: "silent" })], [cred({ id: "c1" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
    const verify = await screen.findByLabelText("Verify first telemetry");
    expect(await within(verify).findByText(/No telemetry for longer than the offline timeout/)).toBeInTheDocument();
    expect(verify).toHaveTextContent("UptimeTrace cannot tell which");
  });

  it("a verification request that fails after the panel loaded says nothing was checked, and does not claim a state", async () => {
    const r = registry([reg({ host_id: "web-01" })], [cred({ id: "c1" })]);
    let failing = false;
    const list = r.api.registryHosts.getMockImplementation()!;
    r.api.registryHosts.mockImplementation(async () => {
      if (failing) throw new ApiError(0, "network_error", "x");
      return list();
    });
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
    const verify = await screen.findByLabelText("Verify first telemetry");
    await within(verify).findByText(/Waiting for the agent/);
    failing = true;
    fireEvent.click(within(verify).getByRole("button", { name: "Check again" }));
    expect(await within(verify).findByText(/nothing has been checked/)).toBeInTheDocument();
    expect(verify).not.toHaveTextContent("Telemetry is late");
  });

  it("first telemetry shows confirmation, the last telemetry time, and links to Command Center and Host Detail", async () => {
    const r = registry([reg({ host_id: "web-01", last_envelope_at: at(MIN), telemetry_state: "reporting" })], [cred({ id: "c1" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
    const done = await screen.findByLabelText("Connected");
    expect(done).toHaveTextContent("First telemetry received");
    expect(done).toHaveTextContent("from web-01");
    expect(within(done).getByRole("link", { name: "Command Center" })).toHaveAttribute("href", "/");
    expect(within(done).getByRole("link", { name: "Open host detail" })).toHaveAttribute("href", "/hosts/web-01");
    expect(screen.queryByLabelText("Verify first telemetry")).not.toBeInTheDocument();
  });

  it("a host whose state says reporting but that has never sent an envelope is not treated as connected", async () => {
    const r = registry([reg({ host_id: "web-01", telemetry_state: "reporting", last_envelope_at: null })], [cred({ id: "c1" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
    expect(await screen.findByLabelText("Verify first telemetry")).toBeInTheDocument();
    expect(screen.queryByLabelText("Connected")).not.toBeInTheDocument();
  });

  it("polls while the verification step is shown, and stops when the page is left", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const r = registry([reg({ host_id: "web-01" })], [cred({ id: "c1" })]);
      const { unmount } = renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
      await screen.findByLabelText("Verify first telemetry");
      const before = r.calls.list;
      await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
      expect(r.calls.list).toBeGreaterThan(before);
      unmount();
      const afterUnmount = r.calls.list;
      await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
      expect(r.calls.list).toBe(afterUnmount);
    } finally {
      vi.useRealTimers();
    }
  });

  it("check again refetches the registry on demand", async () => {
    const r = registry([reg({ host_id: "web-01" })], [cred({ id: "c1" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
    const verify = await screen.findByLabelText("Verify first telemetry");
    await within(verify).findByText(/Waiting for the agent/);
    const before = r.calls.list;
    fireEvent.click(within(verify).getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(r.calls.list).toBeGreaterThan(before));
  });
});

describe("Onboarding: resuming and thresholds", () => {
  it("an interrupted setup resumes from the server state: a host with a credential goes straight to install and verify", async () => {
    const r = registry([reg({ host_id: "web-01" })], [cred({ id: "c1" })]);
    renderApp(page, { api: fakeApi(r.api as never), route: "/onboarding?host=web-01" });
    expect(await screen.findByLabelText("Verify first telemetry")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Register host" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create agent credential" })).not.toBeInTheDocument();
  });

  it("shows the saved thresholds and links to Alert Policies to change them, without saving anything", async () => {
    const r = registry([reg({ host_id: "web-01" })], [cred({ id: "c1" })]);
    const save = vi.fn();
    renderApp(page, { api: fakeApi({ ...r.api, monitoringSettings: async () => SETTINGS, saveMonitoringSettings: save as never } as never), route: "/onboarding?host=web-01" });
    const section = await screen.findByLabelText("Review thresholds");
    expect(await within(section).findByText("Built-in defaults are in effect.")).toBeInTheDocument();
    expect(section).toHaveTextContent("CPU utilisation: warning ≥ 85% · critical ≥ 95%");
    expect(within(section).getByRole("link", { name: "Change them in Alert Policies" })).toHaveAttribute("href", "/settings/alerts");
    expect(save).not.toHaveBeenCalled();
  });
});
