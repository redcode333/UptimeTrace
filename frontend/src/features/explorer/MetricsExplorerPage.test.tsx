import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { fakeApi, renderApp } from "../../test/harness";
import { host, hostList, seriesOf } from "../../test/fixtures";
import { MetricsExplorerPage, statsOf } from "./MetricsExplorerPage";

const a = host({ host_id: "a-1", hostname: "alpha" });
const b = host({ host_id: "sim0002-b", hostname: "beta", source: "synthetic" });
type Call = [string, { metric: string; agg: string; start: string; end: string }];

function setup(hosts = [a, b], values = [5, 6, 7]) {
  const series = vi.fn(async (id: string, p: { metric: string }) => seriesOf(id, p.metric, values));
  return { series, api: fakeApi({ hosts: async () => hostList(hosts), series: series as never }) };
}
const last = (s: { mock: { calls: unknown[][] } }) => s.mock.calls.at(-1) as unknown as Call;
const hostIds = (s: { mock: { calls: unknown[][] } }) => s.mock.calls.map((c) => c[0] as string);

describe("Metrics Explorer", () => {
  it("queries the series API for the selected host, metric, aggregation and range", async () => {
    const { series, api } = setup();
    renderApp(<MetricsExplorerPage />, { api, route: "/metrics?range=24h" });
    await waitFor(() => expect(series).toHaveBeenCalled());
    expect(last(series)[0]).toBe("a-1");
    expect(last(series)[1]).toMatchObject({ metric: "system.cpu.utilization", agg: "avg" });
    expect(Date.parse(last(series)[1].end) - Date.parse(last(series)[1].start)).toBe(24 * 3_600_000);

    await userEvent.click(await screen.findByRole("checkbox", { name: /^alpha/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /^beta/ }));
    await waitFor(() => expect(hostIds(series)).toContain("sim0002-b"));
    await userEvent.selectOptions(screen.getByLabelText("Metric"), "system.network.tx_bytes");
    await waitFor(() => expect(last(series)[1]).toMatchObject({ metric: "system.network.tx_bytes" }));
    await userEvent.selectOptions(screen.getByLabelText("Aggregation"), "max");
    await waitFor(() => expect(last(series)[1]).toMatchObject({ agg: "max", metric: "system.network.tx_bytes" }));
  });

  it("restores host, metric and aggregation from the URL", async () => {
    const { series, api } = setup();
    renderApp(<MetricsExplorerPage />, { api, route: "/metrics?host=sim0002-b&metric=system.memory.utilization&agg=min" });
    await waitFor(() => expect(series).toHaveBeenCalled());
    expect(hostIds(series)).toContain("sim0002-b");
    expect(last(series)[1]).toMatchObject({ metric: "system.memory.utilization", agg: "min" });
    expect(screen.getByRole("checkbox", { name: /^beta/ })).toBeChecked();
  });

  it("asks only for hosts inside the global source scope", async () => {
    const hosts = vi.fn(async () => hostList([b]));
    const series = async (id: string, p: { metric: string }) => seriesOf(id, p.metric, [1]);
    renderApp(<MetricsExplorerPage />, { api: fakeApi({ hosts, series: series as never }), route: "/metrics?source=synthetic" });
    await screen.findByRole("checkbox", { name: /^beta/ });
    expect((hosts.mock.calls[0] as unknown[])[0]).toMatchObject({ source: ["synthetic"] });
    expect(screen.queryByRole("checkbox", { name: /^alpha/ })).not.toBeInTheDocument();
  });

  it("never swaps a host from the URL that is outside the source scope for another host", async () => {
    const { series } = setup([b]);
    const lookup = vi.fn(async () => a);
    renderApp(<MetricsExplorerPage />, { api: fakeApi({ hosts: async () => hostList([b]), series: series as never, host: lookup as never }), route: "/metrics?host=a-1&source=synthetic" });
    expect(await screen.findByText(/a-1 is LIVE, which is outside the selected sources/)).toBeInTheDocument();
    expect(hostIds(series)).not.toContain("sim0002-b");
    expect(hostIds(series)).not.toContain("a-1");
  });

  it("draws several hosts, each with its own series, and reports the statistics from valid points only", async () => {
    const withGap = async (id: string, p: { metric: string }) =>
      seriesOf(id, p.metric, id === "a-1" ? [5, null, 7] : [10, 20, 30]);
    renderApp(<MetricsExplorerPage />, { api: fakeApi({ hosts: async () => hostList([a, b]), series: withGap as never }), route: "/metrics?host=a-1,sim0002-b" });
    const table = await screen.findByLabelText("Series statistics");
    const alphaRow = within(table).getByText("alpha").closest("tr")!;
    expect(alphaRow).toHaveTextContent("5.0%");   // min over 5 and 7, the gap is not zero
    expect(alphaRow).toHaveTextContent("6.0%");   // average of the two valid points
    expect(alphaRow).toHaveTextContent("7.0%");   // max and last
    const betaRow = within(table).getByText("beta").closest("tr")!;
    expect(betaRow).toHaveTextContent("20.0%");
  });

  it("overlays the previous window when Compare is set, and asks for it over the same-length window before", async () => {
    const { series, api } = setup([a]);
    renderApp(<MetricsExplorerPage />, { api, route: "/metrics?host=a-1&range=6h&compare=previous" });
    await waitFor(() => expect(series.mock.calls.length).toBeGreaterThanOrEqual(2));
    const windows = series.mock.calls.map((c) => c[1] as unknown as { start: string; end: string });
    const [previous, current] = [...windows].sort((x, y) => Date.parse(x.end) - Date.parse(y.end));
    expect(Date.parse(current!.end) - Date.parse(current!.start)).toBe(6 * 3_600_000);
    expect(Date.parse(previous!.end) - Date.parse(previous!.start)).toBe(6 * 3_600_000);
    expect(Date.parse(current!.start) - Date.parse(previous!.end)).toBeLessThan(2_000);
    expect(await screen.findByLabelText(/previous period overlaid/)).toBeInTheDocument();
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

  it("states that export, extra queries and forecasts are not built, rather than offering them", async () => {
    const { api } = setup([a]);
    renderApp(<MetricsExplorerPage />, { api, route: "/metrics?host=a-1" });
    expect(await screen.findByRole("button", { name: "Export (not built)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add query (not built)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Forecast (not available)" })).toBeDisabled();
  });
});

describe("statsOf", () => {
  const point = (t: string, value: number | null) => ({ t, value, samples: value == null ? 0 : 1 });
  it("ignores gaps and reports the last valid point", () => {
    const s = statsOf([point("2026-11-20T10:00:00Z", 4), point("2026-11-20T10:01:00Z", null), point("2026-11-20T10:02:00Z", 8)]);
    expect(s).toMatchObject({ min: 4, max: 8, avg: 6, last: 8, lastAt: "2026-11-20T10:02:00Z" });
  });
  it("is null when there is no valid point, so the table shows a dash, not zero", () => {
    expect(statsOf([{ t: "2026-11-20T10:00:00Z", value: null, samples: 0 }])).toBeNull();
    expect(statsOf(undefined)).toBeNull();
  });
});
