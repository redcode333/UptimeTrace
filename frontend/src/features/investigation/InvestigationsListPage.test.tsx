import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/client";
import type { Alert, AlertList, AlertStatusFilter } from "../../api/types";
import { fakeApi, renderApp } from "../../test/harness";
import { InvestigationsListPage, investigationRows, lifecycleLabel } from "./InvestigationsListPage";

const alert = (over: Partial<Alert> & { id: string }): Alert => ({
  host_id: "web-1", condition_key: "system.cpu.utilization|{}", rule: "threshold", metric: "system.cpu.utilization", labels: {},
  severity: "critical", state: "open", value: 97, threshold: 95, opened_at: "2026-11-11T12:00:00Z", last_evaluated_at: "2026-11-11T12:10:00Z",
  acknowledged_at: null, acknowledged_by: null, resolved_at: null, resolved_by: null, resolution: null, ...over,
});

const A = alert({ id: "a1111111-0000-4000-8000-000000000001", host_id: "web-1", severity: "critical", state: "open", opened_at: "2026-11-11T12:00:00Z" });
const B = alert({ id: "b2222222-0000-4000-8000-000000000002", host_id: "db-1", severity: "warning", state: "acknowledged", opened_at: "2026-11-11T11:00:00Z", metric: "system.memory.utilization", value: 90, threshold: 85 });
const C = alert({ id: "c3333333-0000-4000-8000-000000000003", host_id: "web-2", severity: "critical", state: "resolved", resolution: "recovered", opened_at: "2026-11-11T10:00:00Z", resolved_at: "2026-11-11T10:30:00Z" });

function api(all: Alert[], total?: number, calls: { status: string; limit?: number }[] = []) {
  return fakeApi({
    alerts: (async (status: AlertStatusFilter = "active", _signal?: AbortSignal, limit?: number): Promise<AlertList> => {
      calls.push({ status, limit });
      const pool = status === "active" ? all.filter((a) => a.state !== "resolved") : status === "resolved" ? all.filter((a) => a.state === "resolved") : all;
      return { alerts: pool, total: total ?? pool.length };
    }) as never,
  });
}

const rowsOf = () => within(screen.getByRole("table", { name: "Investigations" })).getAllByRole("row").slice(1);

describe("Investigations list", () => {
  it("lists the active investigations by default, with host, trigger, severity and lifecycle", async () => {
    const calls: { status: string; limit?: number }[] = [];
    renderApp(<InvestigationsListPage />, { api: api([A, B, C], undefined, calls), route: "/investigations" });
    expect(await screen.findByText("2 of 2 active investigations.")).toBeInTheDocument();
    expect(calls[0]).toMatchObject({ status: "active", limit: 200 });
    expect(rowsOf()).toHaveLength(2);
    expect(within(rowsOf()[0]!).getByRole("link", { name: "web-1" })).toHaveAttribute("href", "/hosts/web-1");
    expect(rowsOf()[1]).toHaveTextContent("Acknowledged");
  });

  it("switches the lifecycle filter on the server and shows resolved outcomes", async () => {
    const calls: { status: string; limit?: number }[] = [];
    renderApp(<InvestigationsListPage />, { api: api([A, B, C], undefined, calls), route: "/investigations" });
    await screen.findByText("2 of 2 active investigations.");
    fireEvent.click(screen.getByRole("button", { name: "Resolved" }));
    await waitFor(() => expect(calls.at(-1)?.status).toBe("resolved"));
    expect(await screen.findByText("1 of 1 resolved investigation.")).toBeInTheDocument();
    expect(rowsOf()[0]).toHaveTextContent("Resolved: recovered");
  });

  it("searches the loaded rows by host and says what matched", async () => {
    renderApp(<InvestigationsListPage />, { api: api([A, B, C]), route: "/investigations?status=all" });
    await screen.findByText("3 of 3 all investigations.");
    fireEvent.change(screen.getByRole("searchbox", { name: "Search investigations" }), { target: { value: "db-1" } });
    expect(await screen.findByText(/1 of 3 all investigations match "db-1"/)).toBeInTheDocument();
    expect(rowsOf()).toHaveLength(1);
  });

  it("sorts by severity first, and by host name when chosen", async () => {
    renderApp(<InvestigationsListPage />, { api: api([A, B, C]), route: "/investigations?status=all" });
    await screen.findByText("3 of 3 all investigations.");
    expect(rowsOf()[0]).toHaveTextContent("web-1");
    fireEvent.change(screen.getByRole("combobox", { name: "Sort investigations" }), { target: { value: "host" } });
    await waitFor(() => expect(rowsOf()[0]).toHaveTextContent("db-1"));
  });

  it("states plainly when only the most recent rows are loaded, and that search and sort cover those only", async () => {
    renderApp(<InvestigationsListPage />, { api: api([A, B], 450), route: "/investigations" });
    const note = await screen.findByText(/The workspace holds 450/);
    expect(note).toHaveTextContent("search and sort cover only the loaded rows");
  });

  it("explains an empty result for the chosen status", async () => {
    renderApp(<InvestigationsListPage />, { api: api([]), route: "/investigations" });
    expect(await screen.findByText("No active investigations")).toBeInTheDocument();
  });

  it("shows an error, not an empty table, when the list cannot be loaded", async () => {
    const failing = fakeApi({ alerts: (async () => { throw new ApiError(500, "internal", "down"); }) as never });
    renderApp(<InvestigationsListPage />, { api: failing, route: "/investigations" });
    await waitFor(() => expect(screen.queryByText("Loading investigations")).not.toBeInTheDocument());
    expect(screen.queryByRole("table", { name: "Investigations" })).not.toBeInTheDocument();
  });
});

describe("investigation helpers", () => {
  it("names the lifecycle from the state and how a resolved alert ended", () => {
    expect(lifecycleLabel(A)).toBe("Open");
    expect(lifecycleLabel(B)).toBe("Acknowledged");
    expect(lifecycleLabel(C)).toBe("Resolved: recovered");
    expect(lifecycleLabel({ ...C, resolution: "manual" })).toBe("Resolved: by hand");
  });
  it("sorts by opened time newest first by default, with a stable tie-break on id", () => {
    expect(investigationRows([C, A, B], "", "opened_desc").map((a) => a.id)).toEqual([A.id, B.id, C.id]);
    expect(investigationRows([C, A, B], "", "opened_asc").map((a) => a.id)).toEqual([C.id, B.id, A.id]);
  });
});
