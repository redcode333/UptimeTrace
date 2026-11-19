import { describe, expect, it } from "vitest";
import type { MonitoringSettings } from "../api/types";
import { configFrom, draftFrom, isChanged, isDefault, parseNumber, validateDraft, type Draft } from "./monitoringSettings";

const SAVED: MonitoringSettings["config"] = {
  freshness: { stale_after_seconds: 90, offline_after_seconds: 300 },
  rules: {
    "system.cpu.utilization": { warning: 85, critical: 95 },
    "system.memory.utilization": { warning: 85, critical: 95 },
    "system.disk.utilization": { warning: 90, critical: 95 },
  },
};

const valid = (): Draft => draftFrom(SAVED);

describe("draft round trip", () => {
  it("builds a draft from saved values and returns the same document unchanged", () => {
    expect(configFrom(draftFrom(SAVED))).toEqual(SAVED);
  });

  it("is valid and unchanged when the draft is the saved state", () => {
    expect(validateDraft(valid())).toEqual({ valid: true, errors: {} });
    expect(isChanged(valid(), SAVED)).toBe(false);
  });
});

describe("parseNumber", () => {
  it("accepts a whole number and a decimal, and rejects blanks and text", () => {
    expect(parseNumber("85")).toBe(85);
    expect(parseNumber(" 85.5 ")).toBe(85.5);
    expect(parseNumber("")).toBeNull();
    expect(parseNumber("   ")).toBeNull();
    expect(parseNumber("8a")).toBeNull();
    expect(parseNumber("Infinity")).toBeNull();
  });
});

describe("resource thresholds (warning < critical <= 100, warning > 0)", () => {
  const withRule = (warning: string, critical: string): Draft => {
    const d = valid();
    d.rules["system.cpu.utilization"] = { warning, critical };
    return d;
  };

  it("accepts a warning just above zero and a critical of exactly 100", () => {
    expect(validateDraft(withRule("0.1", "100")).valid).toBe(true);
  });

  it("rejects a warning that is not below the critical limit", () => {
    const v = validateDraft(withRule("95", "95"));
    expect(v.valid).toBe(false);
    expect(v.errors["system.cpu.utilization.critical"]).toBe("Must be above the warning limit.");
  });

  it("rejects a warning above the critical limit", () => {
    expect(validateDraft(withRule("96", "95")).valid).toBe(false);
  });

  it("rejects a warning of zero or less", () => {
    expect(validateDraft(withRule("0", "95")).errors["system.cpu.utilization.warning"]).toBe("Must be above 0%.");
  });

  it("rejects a critical above 100", () => {
    expect(validateDraft(withRule("85", "100.5")).errors["system.cpu.utilization.critical"]).toBe("Must be at most 100%.");
  });

  it("rejects blank or non-numeric limits, naming the field", () => {
    const blank = validateDraft(withRule("", "95"));
    expect(blank.errors["system.cpu.utilization.warning"]).toBe("Enter a percentage.");
    const text = validateDraft(withRule("abc", "95"));
    expect(text.errors["system.cpu.utilization.warning"]).toBe("Enter a number.");
  });

  it("never reports the critical relation when a value is missing", () => {
    const v = validateDraft(withRule("", "95"));
    expect(v.errors["system.cpu.utilization.critical"]).toBeUndefined();
  });
});

describe("freshness timeouts (stale 10..3600 whole seconds; offline <= 86400 and > stale)", () => {
  const withTimes = (stale: string, offline: string): Draft => ({ ...valid(), stale, offline });

  it("accepts the stale bounds 10 and 3600", () => {
    expect(validateDraft(withTimes("10", "300")).valid).toBe(true);
    expect(validateDraft(withTimes("3600", "86400")).valid).toBe(true);
  });

  it("rejects a stale timeout below 10 or above 3600, or a fraction of a second", () => {
    expect(validateDraft(withTimes("9", "300")).errors.stale).toBe("Whole seconds from 10 to 3600.");
    expect(validateDraft(withTimes("3601", "86400")).errors.stale).toBe("Whole seconds from 10 to 3600.");
    expect(validateDraft(withTimes("85.5", "300")).errors.stale).toBe("Whole seconds from 10 to 3600.");
  });

  it("rejects an offline timeout above 86400", () => {
    expect(validateDraft(withTimes("90", "86401")).errors.offline).toBe("Whole seconds, at most 86400.");
  });

  it("requires offline to be strictly longer than stale", () => {
    expect(validateDraft(withTimes("90", "90")).errors.offline).toBe("Must be longer than the stale timeout.");
    expect(validateDraft(withTimes("90", "89")).errors.offline).toBe("Must be longer than the stale timeout.");
    expect(validateDraft(withTimes("90", "91")).valid).toBe(true);
  });

  it("does not compare offline with stale when stale is itself invalid", () => {
    const v = validateDraft(withTimes("5", "300"));
    expect(v.errors.stale).toBeDefined();
    expect(v.errors.offline).toBeUndefined();
  });
});

describe("change detection and defaults", () => {
  it("compares parsed values, so 85 and 85.0 are the same setting", () => {
    const d = valid();
    d.rules["system.cpu.utilization"] = { warning: "85.0", critical: "95" };
    expect(isChanged(d, SAVED)).toBe(false);
  });

  it("detects a real change to any field", () => {
    const d = valid();
    d.stale = "120";
    expect(isChanged(d, SAVED)).toBe(true);
  });

  it("says whether the saved configuration is the built-in default", () => {
    expect(isDefault(SAVED, SAVED)).toBe(true);
    expect(isDefault({ ...SAVED, freshness: { stale_after_seconds: 120, offline_after_seconds: 300 } }, SAVED)).toBe(false);
  });
});

describe("configFrom", () => {
  it("produces numbers, with the freshness windows as integers", () => {
    const d = valid();
    d.rules["system.disk.utilization"] = { warning: "88.5", critical: "96" };
    const c = configFrom(d);
    expect(c.rules["system.disk.utilization"]).toEqual({ warning: 88.5, critical: 96 });
    expect(c.freshness).toEqual({ stale_after_seconds: 90, offline_after_seconds: 300 });
    expect(Number.isInteger(c.freshness.stale_after_seconds)).toBe(true);
  });
});
