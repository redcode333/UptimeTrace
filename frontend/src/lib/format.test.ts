import { describe, expect, it } from "vitest";
import { DASH, bytesPerSecond, pct } from "./format";

describe("formatting never turns a missing value into zero", () => {
  it("renders null, undefined and NaN as a dash, and a real 0 as 0", () => {
    expect(pct(null)).toBe(DASH);
    expect(pct(undefined)).toBe(DASH);
    expect(bytesPerSecond(null)).toBe(DASH);
    expect(bytesPerSecond(Number.NaN)).toBe(DASH);
    expect(pct(0)).toBe("0.0%");
    expect(bytesPerSecond(0)).toBe("0 B/s");
  });

  it("scales byte rates in decimal units", () => {
    expect(bytesPerSecond(1500)).toBe("1.5 KB/s");
    expect(bytesPerSecond(2_500_000)).toBe("2.5 MB/s");
  });
});
