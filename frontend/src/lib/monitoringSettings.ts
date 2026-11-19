/**
 * Alert Policies: form model and validation. The limits below are copied from the backend's parse_document
 * (backend/app/monitoring/settings.py: STALE_RANGE, OFFLINE_MAX and the rule checks). The server is still the authority:
 * a save is only shown as successful after the server accepts it. These checks exist so an operator sees the problem first.
 */
import type { MonitoringSettings } from "../api/types";

export const STALE_MIN_SECONDS = 10;
export const STALE_MAX_SECONDS = 3600;
export const OFFLINE_MAX_SECONDS = 86400;
export const PERCENT_MAX = 100;

export const RULE_METRICS = [
  { key: "system.cpu.utilization", label: "CPU utilisation" },
  { key: "system.memory.utilization", label: "Memory utilisation" },
  { key: "system.disk.utilization", label: "Disk utilisation (worst mount)" },
] as const;

export type RuleKey = (typeof RULE_METRICS)[number]["key"];
export interface RuleDraft { warning: string; critical: string }
export interface Draft {
  rules: Record<RuleKey, RuleDraft>;
  stale: string;
  offline: string;
}

export type Config = MonitoringSettings["config"];

/** The draft as text, exactly as saved. Numbers are rendered in full so no precision is lost. */
export function draftFrom(config: Config): Draft {
  const rules = {} as Record<RuleKey, RuleDraft>;
  for (const m of RULE_METRICS) {
    const r = config.rules[m.key];
    rules[m.key] = { warning: r ? String(r.warning) : "", critical: r ? String(r.critical) : "" };
  }
  return {
    rules,
    stale: String(config.freshness.stale_after_seconds),
    offline: String(config.freshness.offline_after_seconds),
  };
}

/** Strict number parse: the whole trimmed string must be a finite number. */
export function parseNumber(text: string): number | null {
  const t = text.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

function parseWhole(text: string): number | null {
  const n = parseNumber(text);
  return n !== null && Number.isInteger(n) ? n : null;
}

export interface Validation {
  valid: boolean;
  /** Field errors keyed by field path, e.g. "rules.system.cpu.utilization.warning" or "stale". */
  errors: Record<string, string>;
}

export function validateDraft(d: Draft): Validation {
  const errors: Record<string, string> = {};

  for (const m of RULE_METRICS) {
    const r = d.rules[m.key];
    const w = parseNumber(r.warning);
    const c = parseNumber(r.critical);
    if (w === null) errors[`${m.key}.warning`] = r.warning.trim() === "" ? "Enter a percentage." : "Enter a number.";
    if (c === null) errors[`${m.key}.critical`] = r.critical.trim() === "" ? "Enter a percentage." : "Enter a number.";
    if (w !== null && w <= 0) errors[`${m.key}.warning`] = "Must be above 0%.";
    if (c !== null && c > PERCENT_MAX) errors[`${m.key}.critical`] = `Must be at most ${PERCENT_MAX}%.`;
    if (w !== null && c !== null && !errors[`${m.key}.warning`] && !errors[`${m.key}.critical`] && !(w < c)) {
      errors[`${m.key}.critical`] = "Must be above the warning limit.";
    }
  }

  const stale = parseWhole(d.stale);
  if (stale === null || stale < STALE_MIN_SECONDS || stale > STALE_MAX_SECONDS) {
    errors.stale = `Whole seconds from ${STALE_MIN_SECONDS} to ${STALE_MAX_SECONDS}.`;
  }
  const offline = parseWhole(d.offline);
  if (offline === null || offline > OFFLINE_MAX_SECONDS) {
    errors.offline = `Whole seconds, at most ${OFFLINE_MAX_SECONDS}.`;
  } else if (stale !== null && stale >= STALE_MIN_SECONDS && stale <= STALE_MAX_SECONDS && !(offline > stale)) {
    errors.offline = "Must be longer than the stale timeout.";
  }

  return { valid: Object.keys(errors).length === 0, errors };
}

/** The document the server expects, built only from a draft that passed validation. */
export function configFrom(d: Draft): Config {
  const rules = {} as Config["rules"];
  for (const m of RULE_METRICS) {
    rules[m.key] = { warning: Number(d.rules[m.key].warning), critical: Number(d.rules[m.key].critical) };
  }
  return {
    freshness: { stale_after_seconds: Number(d.stale), offline_after_seconds: Number(d.offline) },
    rules,
  };
}

/** Whether the draft differs from what the server holds, compared on parsed values. */
export function isChanged(d: Draft, saved: Config): boolean {
  const s = draftFrom(saved);
  return RULE_METRICS.some((m) => {
    const a = d.rules[m.key], b = s.rules[m.key];
    return parseNumber(a.warning) !== parseNumber(b.warning) || parseNumber(a.critical) !== parseNumber(b.critical);
  }) || parseWhole(d.stale) !== parseWhole(s.stale) || parseWhole(d.offline) !== parseWhole(s.offline);
}

/** Whether the saved configuration equals the built-in defaults (nothing customised). */
export function isDefault(config: Config, defaults: Config): boolean {
  return JSON.stringify(config) === JSON.stringify(defaults);
}
