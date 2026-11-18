import { describe, expect, it } from "vitest";
import { applyHostsView, parseHostsView } from "./hostsQuery";

describe("hosts view <-> URL", () => {
  it("defaults to newest-first page 1 and ignores junk values", () => {
    expect(parseHostsView(new URLSearchParams("health=bogus&page=-4&sort=cpu&order=sideways"))).toEqual({ search: "", health: [], sort: "last_seen", order: "desc", page: 1 });
  });

  it("hostname sorts ascending by default and keeps an explicit order", () => {
    expect(parseHostsView(new URLSearchParams("sort=hostname")).order).toBe("asc");
    expect(parseHostsView(new URLSearchParams("sort=hostname&order=desc")).order).toBe("desc");
  });

  it("any filter change returns to page 1, but paging keeps the filters and the global scope", () => {
    const base = new URLSearchParams("source=live&range=24h&health=critical&page=3");
    const filtered = applyHostsView(base, { search: "web" });
    expect(filtered.get("page")).toBeNull();
    expect(filtered.get("search")).toBe("web");
    expect(filtered.get("source")).toBe("live");
    expect(filtered.get("range")).toBe("24h");
    const paged = applyHostsView(base, { page: 4 });
    expect(paged.get("page")).toBe("4");
    expect(paged.get("health")).toBe("critical");
  });

  it("drops default values from the URL", () => {
    expect(applyHostsView(new URLSearchParams("sort=hostname&order=desc"), { sort: "last_seen", order: "desc" }).toString()).toBe("");
  });
});
