import { describe, expect, it } from "vitest";
import { parseScope, toggleSource, withScope } from "./scope";
import { DEFAULT_RANGE, globalSearch, parseRange } from "./timeRange";

describe("scope", () => {
  it("defaults to all three sources and ignores unknown or empty values", () => {
    expect(parseScope(new URLSearchParams(""))).toEqual(["live", "synthetic", "replay"]);
    expect(parseScope(new URLSearchParams("source=cloud"))).toEqual(["live", "synthetic", "replay"]);
    expect(parseScope(new URLSearchParams("source=replay,LIVE,cloud"))).toEqual(["live", "replay"]);
  });

  it("never lets the last selected source be turned off", () => {
    expect(toggleSource(["live"], "live")).toEqual(["live"]);
    expect(toggleSource(["live", "replay"], "live")).toEqual(["replay"]);
    expect(toggleSource(["replay"], "live")).toEqual(["live", "replay"]);
  });

  it("writes the scope to the URL only when it is not the default, keeping other params", () => {
    const all = withScope(new URLSearchParams("range=24h&source=live"), ["live", "synthetic", "replay"]);
    expect(all.get("source")).toBeNull();
    expect(all.get("range")).toBe("24h");
    expect(withScope(new URLSearchParams(), ["synthetic", "replay"]).get("source")).toBe("synthetic,replay");
  });
});

describe("time range and global params", () => {
  it("falls back to the default range for unknown values", () => {
    expect(parseRange(new URLSearchParams("range=24h"))).toBe("24h");
    expect(parseRange(new URLSearchParams("range=forever"))).toBe(DEFAULT_RANGE);
  });

  it("carries only scope and range between pages", () => {
    expect(globalSearch(new URLSearchParams("source=live&range=7d&search=x&page=3"))).toBe("?source=live&range=7d");
    expect(globalSearch(new URLSearchParams("page=3"))).toBe("");
  });
});
