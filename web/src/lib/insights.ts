// Insights helpers: URL filters, number formatting (whole %, points), and links into the sessions list.
import { defaultStringifySearch } from "@tanstack/react-router";
import { type InsightFilters, type SessionFilters, WINDOWS } from "./api";
import { wsHref } from "./workspace";

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);
const oneOf = <T extends string>(v: unknown, options: readonly T[]) => (options.includes(v as T) ? (v as T) : undefined);
const nums = (v: unknown): number[] | undefined => {
  const list = (Array.isArray(v) ? v : v == null || v === "" ? [] : [v]).map(Number).filter((n) => Number.isInteger(n));
  return list.length ? list : undefined;
};

export function validateInsightSearch(s: Record<string, unknown>): InsightFilters {
  return {
    window: oneOf(s.window, WINDOWS),
    agent: str(s.agent),
    procedure: str(s.procedure),
    release: nums(s.release),
    channel: oneOf(s.channel, ["voice", "text"] as const),
  };
}

export const WINDOW_LABEL: Record<(typeof WINDOWS)[number], string> = { "24h": "24 hours", "7d": "7 days", "30d": "30 days", "90d": "90 days", "365d": "365 days" };

/** A whole percentage, or "—" when there's no denominator. */
export const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v}%`);

/** A delta in points ("+4 pts") or % ("+12%"). */
export function fmtDelta(v: number | null | undefined, unit: "pts" | "%"): string {
  if (v == null) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "±";
  return unit === "pts" ? `${sign}${Math.abs(v)} pts` : `${sign}${Math.abs(v)}%`;
}

/** "YYYY-MM-DDTHH:mm" in local time, as the sessions list's datetime inputs keep it. */
function local(iso: string, addMinutes = 0): string {
  const d = new Date(new Date(iso).getTime() + addMinutes * 60_000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** The window and filters an Insights response was computed for, as sessions-list filters. */
export type Scope = { start: string; end: string; agent?: string | null; releases?: number[]; channel?: string | null };

export function scopeFilters(scope: Scope | undefined): SessionFilters {
  if (!scope) return {};
  return {
    since: local(scope.start),
    until: local(scope.end, 1), // the list's `until` is exclusive and minute-precise
    agent: scope.agent ?? undefined,
    channel: (scope.channel as SessionFilters["channel"]) ?? undefined,
    release: scope.releases?.length === 1 ? scope.releases[0] : undefined,
  };
}

/** A link to the sessions list with these filters (drop empty ones). */
export function sessionsHref(ws: string, filters: SessionFilters, scope?: Scope): string {
  const all: Record<string, unknown> = { ...scopeFilters(scope), ...filters };
  for (const k of Object.keys(all)) if (all[k] == null || all[k] === "" || (Array.isArray(all[k]) && !(all[k] as unknown[]).length)) delete all[k];
  return wsHref(ws, "sessions") + defaultStringifySearch(all);
}

/** A link to an Insights page that keeps the current filters. */
export function insightsHref(ws: string, path: (string | number)[], f: InsightFilters): string {
  const all: Record<string, unknown> = { ...f };
  for (const k of Object.keys(all)) if (all[k] == null || (Array.isArray(all[k]) && !(all[k] as unknown[]).length)) delete all[k];
  return wsHref(ws, ...path) + defaultStringifySearch(all);
}

export const KIND_LABEL = { step: "Step", never: "Never", warning: "Warning sign" } as const;
export const ruleName = (kind: string | null | undefined, index: number | null | undefined) =>
  kind ? `${KIND_LABEL[kind as keyof typeof KIND_LABEL] ?? kind} ${index ?? ""}`.trim() : "Rule";

/** Statuses that fail a rule, for drill-downs. */
export const FAIL_STATUSES = { step: ["skipped", "out_of_order"], never: ["violated"], warning: ["unheeded"] } as const;
