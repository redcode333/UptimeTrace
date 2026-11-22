import { fireEvent, screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Host, HostList } from "../../api/types";
import { histogramFake, rankingFake } from "../../test/commandCenterFakes";
import { fakeApi, renderApp } from "../../test/harness";
import { host, hostList } from "../../test/fixtures";
import { OverviewPage } from "./OverviewPage";

const none = { disk_percent_max: null, disk_mount_max: null, network_rx_bytes_per_second: null, network_tx_bytes_per_second: null };
const many = (n: number): Host[] =>
  Array.from({ length: n }, (_, i) => host({ host_id: `h-${String(i).padStart(3, "0")}`, hostname: `h-${String(i).padStart(3, "0")}`, latest: { cpu_percent: i, memory_percent: 10, ...none } } as never));

const routes = (
  <Routes>
    <Route path="/" element={<OverviewPage />} />
    <Route path="/hosts" element={<div>HOSTS PAGE</div>} />
    <Route path="/hosts/:hostId" element={<div>HOST DETAIL PAGE</div>} />
  </Routes>
);

/** A workspace with more hosts than one page: the fleet request returns 200 rows, the summary and LIVE requests report the true total. */
function bigFleet(total: number, loaded: Host[]) {
  return fakeApi({
    hosts: async (p = {}) => {
      if ((p.page_size ?? 0) === 1) {
        return hostList([], { total, facets: { source: { live: total, synthetic: 0, replay: 0 }, health: { healthy: total, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0 } } }) as HostList;
      }
      return hostList(loaded, { total, facets: { source: { live: total, synthetic: 0, replay: 0 }, health: { healthy: total, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0 } } }) as HostList;
    },
    rankings: async (p) => rankingFake(loaded, p.metric, p.limit, p.source),
    alertHistogram: async (p) => histogramFake([], p),
  });
}

describe("Command Center: server-backed counts", () => {
  it("counts connected LIVE hosts from the server total, not from the loaded page", async () => {
    renderApp(routes, { api: bigFleet(250, many(3)), route: "/" });
    const live = await screen.findByLabelText("LIVE only");
    expect(within(live).getByText("Connected hosts").closest("a")).toHaveTextContent("250");
  });

  it("says how many in-scope hosts the map represents, and offers the full list in Hosts", async () => {
    // one full map page (60 hosts) is enough to show the coverage statement; the remaining loaded hosts are not drawn here
    renderApp(routes, { api: bigFleet(250, many(60)), route: "/" });
    expect(await screen.findByText(/Showing hosts 1–60 of 250 in scope; 190 more are not loaded here/)).toBeInTheDocument();
    const inspect = screen.getByRole("link", { name: "Inspect all 250 in Hosts" });
    expect(inspect).toHaveAttribute("href", "/hosts");
  });

  it("pages the map through the loaded hosts with working previous and next controls", async () => {
    renderApp(routes, { api: bigFleet(70, many(70)), route: "/" });
    await screen.findByText(/Showing hosts 1–60 of 70 in scope/);
    const prev = screen.getByRole("button", { name: "Previous 60" });
    const next = screen.getByRole("button", { name: "Next 60" });
    expect(prev).toBeDisabled();
    fireEvent.click(next);
    expect(await screen.findByText(/Showing hosts 61–70 of 70 in scope/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next 60" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Previous 60" }));
    expect(await screen.findByText(/Showing hosts 1–60 of 70 in scope/)).toBeInTheDocument();
  });

  it("opens a host from a later map page at its detail page", async () => {
    renderApp(routes, { api: bigFleet(70, many(70)), route: "/" });
    await screen.findByText(/Showing hosts 1–60 of 70/);
    fireEvent.click(screen.getByRole("button", { name: "Next 60" }));
    await screen.findByText(/Showing hosts 61–70 of 70/);
    const cell = document.querySelector('a[aria-label^="h-069 "]') as HTMLElement;
    expect(cell).not.toBeNull();
    fireEvent.click(cell);
    expect(await screen.findByText("HOST DETAIL PAGE")).toBeInTheDocument();
  });

  it("ranks hosts by the server result and states how many in scope were not ranked", async () => {
    const hosts = [...many(3), host({ host_id: "quiet", hostname: "quiet", latest: { cpu_percent: null, memory_percent: null, ...none } } as never)];
    renderApp(routes, { api: bigFleet(4, hosts), route: "/" });
    const list = await screen.findByRole("list", { name: "CPU now by host" });
    expect(within(list).getAllByRole("link").map((a) => a.textContent)).toEqual(["h-002", "h-001", "h-000"]);
    // the CPU and memory rankings each report their own unranked host
    expect(screen.getAllByText(/1 in scope has no current value and is not ranked/).length).toBeGreaterThan(0);
  });
});

describe("Command Center: loading, failure and zero are never the same", () => {
  it("shows a failed utilisation request as unavailable, never as zero counts", async () => {
    const failing = fakeApi({
      hosts: async () => hostList(many(3), { total: 3, facets: { source: { live: 3, synthetic: 0, replay: 0 }, health: { healthy: 3, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0 } } }) as HostList,
      rankings: async (p) => rankingFake(many(3), p.metric, p.limit, p.source),
      alertHistogram: async (p) => histogramFake([], p),
      distribution: async () => { throw new Error("distribution down"); },
    });
    renderApp(routes, { api: failing, route: "/" });
    const panel = await screen.findByLabelText("Utilisation distribution");
    expect(await within(panel).findAllByText(/could not reach|unavailable|failed/i)).not.toHaveLength(0);
    expect(panel.textContent).not.toMatch(/Warning 0 · Critical 0 · Normal 0/);
  });

  it("shows loading, not zeros, while the utilisation distribution is still arriving", async () => {
    const pending = fakeApi({
      hosts: async () => hostList(many(3), { total: 3, facets: { source: { live: 3, synthetic: 0, replay: 0 }, health: { healthy: 3, warning: 0, critical: 0, stale: 0, offline: 0, unknown: 0 } } }) as HostList,
      rankings: async (p) => rankingFake(many(3), p.metric, p.limit, p.source),
      alertHistogram: async (p) => histogramFake([], p),
      distribution: () => new Promise<never>(() => {}),
    });
    renderApp(routes, { api: pending, route: "/" });
    const panel = await screen.findByLabelText("Utilisation distribution");
    expect(await within(panel).findAllByText(/Loading|Checking/i)).not.toHaveLength(0);
    expect(panel.textContent).not.toMatch(/Normal 0/);
  });
});
