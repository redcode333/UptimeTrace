import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { fakeApi, renderApp } from "../../test/harness";
import { host, hostList, seriesOf } from "../../test/fixtures";
import { MetricsExplorerPage } from "./MetricsExplorerPage";

const a = host({ host_id: "a-1", hostname: "alpha" });
const b = host({ host_id: "sim0002-b", hostname: "beta", source: "synthetic" });
type Call = [string, { metric: string; agg: string; start: string; end: string }];

function setup() {
  const series = vi.fn(async (id: string, p: { metric: string }) => seriesOf(id, p.metric, [5, 6, 7]));
  return { series, api: fakeApi({ hosts: async () => hostList([a, b]), series: series as never }) };
}
const last = (s: { mock: { calls: unknown[][] } }) => s.mock.calls.at(-1) as unknown as Call;

describe("Metrics Explorer", () => {
  it("queries the series API for the selected host, metric, aggregation and range", async () => {
    const { series, api } = setup();
    renderApp(<MetricsExplorerPage />, { api, route: "/metrics?range=24h" });
    await waitFor(() => expect(series).toHaveBeenCalled());
    expect(last(series)[0]).toBe("a-1");
    expect(last(series)[1]).toMatchObject({ metric: "system.cpu.utilization", agg: "avg" });
    expect(Date.parse(last(series)[1].end) - Date.parse(last(series)[1].start)).toBe(24 * 3_600_000);

    await userEvent.selectOptions(screen.getByLabelText("Host"), "sim0002-b");
    await waitFor(() => expect(last(series)[0]).toBe("sim0002-b"));
    await userEvent.selectOptions(screen.getByLabelText("Metric"), "system.network.tx_bytes");
    await waitFor(() => expect(last(series)[1]).toMatchObject({ metric: "system.network.tx_bytes" }));
    await userEvent.selectOptions(screen.getByLabelText("Aggregation"), "max");
    await waitFor(() => expect(last(series)[1]).toMatchObject({ agg: "max", metric: "system.network.tx_bytes" }));
  });

  it("restores host, metric and aggregation from the URL", async () => {
    const { series, api } = setup();
    renderApp(<MetricsExplorerPage />, { api, route: "/metrics?host=sim0002-b&metric=system.memory.utilization&agg=min" });
    await waitFor(() => expect(series).toHaveBeenCalled());
    expect(last(series)[0]).toBe("sim0002-b");
    expect(last(series)[1]).toMatchObject({ metric: "system.memory.utilization", agg: "min" });
  });

  it("asks only for hosts inside the global source scope", async () => {
    const hosts = vi.fn(async () => hostList([b]));
    const series = async (id: string, p: { metric: string }) => seriesOf(id, p.metric, [1]);
    renderApp(<MetricsExplorerPage />, { api: fakeApi({ hosts, series: series as never }), route: "/metrics?source=synthetic" });
    await screen.findByLabelText("Host");
    expect((hosts.mock.calls[0] as unknown[])[0]).toMatchObject({ source: ["synthetic"] });
    expect(screen.getAllByRole("option").some((o) => o.textContent?.includes("alpha"))).toBe(false);
  });

  it("shows an empty-result state", async () => {
    const empty = async (id: string, p: { metric: string }) => seriesOf(id, p.metric, [], { kind: null, series: [] });
    renderApp(<MetricsExplorerPage />, { api: fakeApi({ hosts: async () => hostList([a]), series: empty as never }) });
    expect(await screen.findByText("No data in this window")).toBeInTheDocument();
  });

  it("shows the API's validation message when the series request is rejected", async () => {
    const reject = async () => { throw new ApiError(422, "invalid_request", "bad", [{ path: "step", message: "step too small" }]); };
    renderApp(<MetricsExplorerPage />, { api: fakeApi({ hosts: async () => hostList([a]), series: reject as never }) });
    expect(await screen.findByText("step too small")).toBeInTheDocument();
  });

  it("explains an empty scope", async () => {
    renderApp(<MetricsExplorerPage />, { api: fakeApi({ hosts: async () => hostList([]) }) });
    expect(await screen.findByText("No hosts in scope")).toBeInTheDocument();
  });
});
