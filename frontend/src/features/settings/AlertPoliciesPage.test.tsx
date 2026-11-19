import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { useHostList } from "../../api/hooks";
import type { MonitoringSettings, MonitoringSettingsIn } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { hostList } from "../../test/fixtures";
import { AlertPoliciesPage } from "./AlertPoliciesPage";

const DEFAULT_RULES = {
  "system.cpu.utilization": { warning: 85, critical: 95 },
  "system.memory.utilization": { warning: 85, critical: 95 },
  "system.disk.utilization": { warning: 90, critical: 95 },
};
const DEFAULT_FRESH = { stale_after_seconds: 90, offline_after_seconds: 300 };
const DEFAULTS: MonitoringSettings["config"] = { freshness: DEFAULT_FRESH, rules: DEFAULT_RULES };

const settingsDoc = (over: Partial<MonitoringSettings> = {}): MonitoringSettings => ({
  config: DEFAULTS, defaults: DEFAULTS, version: 0, updated_at: null, updated_by: null, ...over,
});
const saved = (version = 3, cpuWarning = 80): MonitoringSettings => settingsDoc({
  version,
  config: { freshness: { stale_after_seconds: 120, offline_after_seconds: 600 }, rules: { ...DEFAULT_RULES, "system.cpu.utilization": { warning: cpuWarning, critical: 95 } } },
  updated_by: "user_allowed_1",
  updated_at: "2026-11-19T09:00:00Z",
});

const page = <AlertPoliciesPage />;
const field = (label: string) => screen.getByRole("textbox", { name: label }) as HTMLInputElement;

/** A probe that reads the host list, so the test can observe whether a save made the console refetch hosts. */
function HostsProbe() {
  useHostList({ page_size: 1 });
  return null;
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe("Alert Policies: loading and saved values", () => {
  it("loads the saved values and shows the defaults beside them", async () => {
    renderApp(page, { api: fakeApi({ monitoringSettings: async () => saved(3, 80) }), route: "/settings/alerts" });
    expect(await screen.findByRole("textbox", { name: "CPU utilisation warning limit (%)" })).toHaveValue("80");
    expect(field("CPU utilisation critical limit (%)")).toHaveValue("95");
    expect(field("Stale timeout (seconds)")).toHaveValue("120");
    expect(field("Offline timeout (seconds)")).toHaveValue("600");
    const cpuRow = screen.getByText("CPU utilisation").closest("tr")!;
    expect(within(cpuRow).getByText("80 / 95")).toBeInTheDocument(); // saved
    expect(within(cpuRow).getByText("85 / 95")).toBeInTheDocument(); // default
    expect(screen.getByText(/Saved version/)).toHaveTextContent("Saved version 3");
    expect(screen.getByText(/customised/)).toBeInTheDocument();
  });

  it("says the built-in defaults are in effect when nothing has been saved", async () => {
    renderApp(page, { api: fakeApi({ monitoringSettings: async () => settingsDoc() }), route: "/settings/alerts" });
    expect(await screen.findByText(/Built-in defaults are in effect/)).toBeInTheDocument();
  });

  it("does not save anything when the page loads", async () => {
    const save = vi.fn();
    renderApp(page, { api: fakeApi({ monitoringSettings: async () => saved(), saveMonitoringSettings: save as never }), route: "/settings/alerts" });
    await screen.findByRole("textbox", { name: "CPU utilisation warning limit (%)" });
    expect(save).not.toHaveBeenCalled();
  });

  it("shows a loading state, and an error with retry when the settings cannot be loaded", async () => {
    const load = vi.fn(async () => { throw new ApiError(0, "network_error", "x"); });
    renderApp(page, { api: fakeApi({ monitoringSettings: load }), route: "/settings/alerts" });
    expect(await screen.findByText("Could not reach the API")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Alert policy settings" })).not.toBeInTheDocument();
  });

  it("explains warning, critical, stale and offline, and keeps missing telemetry apart from breaches", async () => {
    renderApp(page, { api: fakeApi({ monitoringSettings: async () => saved() }), route: "/settings/alerts" });
    const explain = await screen.findByLabelText("How these policies behave");
    expect(explain).toHaveTextContent("This is a confirmed breach");
    expect(explain).toHaveTextContent("does not mean the machine has crashed");
    expect(explain).toHaveTextContent("cause could be the agent, the network or the machine");
    expect(explain).toHaveTextContent("Stale comes first and offline second");
  });
});

describe("Alert Policies: validation prevents invalid submissions", () => {
  it("shows the warning-versus-critical error inline and disables saving", async () => {
    const save = vi.fn();
    renderApp(page, { api: fakeApi({ monitoringSettings: async () => saved(), saveMonitoringSettings: save as never }), route: "/settings/alerts" });
    fireEvent.change(await screen.findByRole("textbox", { name: "CPU utilisation warning limit (%)" }), { target: { value: "96" } });
    expect(await screen.findByText("Must be above the warning limit.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save policies" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Save policies" }));
    expect(save).not.toHaveBeenCalled();
    expect(screen.getByText(/Fix the highlighted fields before saving/)).toBeInTheDocument();
  });

  it("rejects an offline timeout that is not longer than the stale timeout", async () => {
    renderApp(page, { api: fakeApi({ monitoringSettings: async () => saved() }), route: "/settings/alerts" });
    fireEvent.change(await screen.findByRole("textbox", { name: "Offline timeout (seconds)" }), { target: { value: "120" } });
    expect(await screen.findByText("Must be longer than the stale timeout.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save policies" })).toBeDisabled();
  });

  it("rejects a stale timeout below the minimum", async () => {
    renderApp(page, { api: fakeApi({ monitoringSettings: async () => saved() }), route: "/settings/alerts" });
    fireEvent.change(await screen.findByRole("textbox", { name: "Stale timeout (seconds)" }), { target: { value: "4" } });
    expect(await screen.findByText("Whole seconds from 10 to 3600.")).toBeInTheDocument();
  });
});

describe("Alert Policies: saving", () => {
  it("does not show success until the server confirms, then shows the confirmed values and refreshes hosts", async () => {
    const pending = deferred<MonitoringSettings>();
    const save = vi.fn((_body: MonitoringSettingsIn) => pending.promise);
    const hostCalls: unknown[] = [];
    // A stateful server: what it holds changes only when the save is confirmed, and refetches return what it holds.
    let serverHolds = saved(3, 80);
    const api = fakeApi({
      monitoringSettings: async () => serverHolds,
      saveMonitoringSettings: save as never,
      hosts: async (p = {}) => { hostCalls.push(p); return hostList([]); },
    });
    renderApp(<>{page}<HostsProbe /></>, { api, route: "/settings/alerts" });
    fireEvent.change(await screen.findByRole("textbox", { name: "CPU utilisation warning limit (%)" }), { target: { value: "82" } });
    fireEvent.click(screen.getByRole("button", { name: "Save policies" }));

    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ expected_version: 3 })));
    expect(save.mock.calls[0]![0].config.rules["system.cpu.utilization"]).toEqual({ warning: 82, critical: 95 });
    expect(screen.getByText("Saving…")).toBeInTheDocument();
    expect(screen.queryByText(/Saved as version/)).not.toBeInTheDocument();
    const hostsBefore = hostCalls.length;

    serverHolds = saved(4, 82);
    pending.resolve(serverHolds);
    expect(await screen.findByText(/Saved as version 4/)).toBeInTheDocument();
    expect(field("CPU utilisation warning limit (%)")).toHaveValue("82");
    expect(screen.getByText(/Saved version/)).toHaveTextContent("Saved version 4");
    await waitFor(() => expect(hostCalls.length).toBeGreaterThan(hostsBefore));
  });

  it("shows the server's validation message and keeps the saved values when the save is refused", async () => {
    const save = vi.fn(async () => { throw new ApiError(422, "invalid_request", "Invalid", [{ path: "rules.system.cpu.utilization", message: "Requires 0 < warning < critical <= 100 (percent)." }]); });
    renderApp(page, { api: fakeApi({ monitoringSettings: async () => saved(3, 80), saveMonitoringSettings: save as never }), route: "/settings/alerts" });
    fireEvent.change(await screen.findByRole("textbox", { name: "CPU utilisation warning limit (%)" }), { target: { value: "81" } });
    fireEvent.click(screen.getByRole("button", { name: "Save policies" }));
    expect(await screen.findByText(/Could not save the policies\. Nothing was changed\./)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("rules.system.cpu.utilization: Requires 0 < warning < critical <= 100");
    expect(screen.queryByText(/Saved as version/)).not.toBeInTheDocument();
    expect(field("CPU utilisation warning limit (%)")).toHaveValue("81");
  });

  it("reports a network failure as nothing saved", async () => {
    const save = vi.fn(async () => { throw new ApiError(0, "network_error", "x"); });
    renderApp(page, { api: fakeApi({ monitoringSettings: async () => saved(), saveMonitoringSettings: save as never }), route: "/settings/alerts" });
    fireEvent.change(await screen.findByRole("textbox", { name: "Stale timeout (seconds)" }), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "Save policies" }));
    expect(await screen.findByText("Could not reach the API. Nothing was saved.")).toBeInTheDocument();
  });

  it("on a version conflict, refuses to overwrite and offers a reload of the saved values", async () => {
    let current = saved(3, 80);
    const save = vi.fn(async () => { throw new ApiError(409, "version_conflict", "The settings changed since you loaded them; reload and retry."); });
    const load = vi.fn(async () => current);
    renderApp(page, { api: fakeApi({ monitoringSettings: load, saveMonitoringSettings: save as never }), route: "/settings/alerts" });
    fireEvent.change(await screen.findByRole("textbox", { name: "CPU utilisation warning limit (%)" }), { target: { value: "70" } });
    fireEvent.click(screen.getByRole("button", { name: "Save policies" }));
    expect(await screen.findByText(/changed since this page loaded, so nothing was saved/)).toBeInTheDocument();

    current = saved(5, 88); // another editor saved in the meantime
    fireEvent.click(screen.getByRole("button", { name: "Reload saved values" }));
    await waitFor(() => expect(field("CPU utilisation warning limit (%)")).toHaveValue("88"));
    expect(screen.getByText(/Saved version/)).toHaveTextContent("Saved version 5");
    expect(screen.queryByText(/changed since this page loaded/)).not.toBeInTheDocument();
  });
});

describe("Alert Policies: editing helpers", () => {
  it("discard restores the saved values without saving", async () => {
    const save = vi.fn();
    renderApp(page, { api: fakeApi({ monitoringSettings: async () => saved(3, 80), saveMonitoringSettings: save as never }), route: "/settings/alerts" });
    const input = await screen.findByRole("textbox", { name: "CPU utilisation warning limit (%)" });
    fireEvent.change(input, { target: { value: "70" } });
    fireEvent.click(screen.getByRole("button", { name: "Discard edits" }));
    expect(input).toHaveValue("80");
    expect(save).not.toHaveBeenCalled();
  });

  it("fill in defaults only edits the form; nothing is saved until the operator saves", async () => {
    const save = vi.fn();
    renderApp(page, { api: fakeApi({ monitoringSettings: async () => saved(3, 80), saveMonitoringSettings: save as never }), route: "/settings/alerts" });
    await screen.findByRole("textbox", { name: "CPU utilisation warning limit (%)" });
    fireEvent.click(screen.getByRole("button", { name: "Fill in defaults" }));
    expect(field("CPU utilisation warning limit (%)")).toHaveValue("85");
    expect(field("Stale timeout (seconds)")).toHaveValue("90");
    expect(save).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Save policies" })).toBeEnabled();
  });
});
