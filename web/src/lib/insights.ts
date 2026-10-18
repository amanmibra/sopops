// Insights helpers: URL filters, number formatting (whole %, points), and links into the sessions list.
import { defaultStringifySearch } from "@tanstack/react-router";
import { type InsightFilters, type SessionFilters, WINDOWS } from "./api";
import { wsHref } from "./workspace";

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);
const day = (v: unknown) => (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined);
const oneOf = <T extends string>(v: unknown, options: readonly T[]) => (options.includes(v as T) ? (v as T) : undefined);
const nums = (v: unknown): number[] | undefined => {
  const list = (Array.isArray(v) ? v : v == null || v === "" ? [] : [v]).map(Number).filter((n) => Number.isInteger(n));
  return list.length ? list : undefined;
};

export function validateInsightSearch(s: Record<string, unknown>): InsightFilters {
  const start = day(s.start);
  return {
    window: start ? undefined : oneOf(s.window, WINDOWS),
    start,
    end: start ? day(s.end) : undefined,
    agent: str(s.agent),
    procedure: str(s.procedure),
    release: nums(s.release),
    channel: oneOf(s.channel, ["voice", "text"] as const),
  };
}

export const WINDOW_LABEL: Record<(typeof WINDOWS)[number], string> = { "24h": "24 hours", "7d": "7 days", "30d": "30 days", "90d": "90 days", "365d": "365 days" };
export const WINDOW_SHORT: Record<(typeof WINDOWS)[number], string> = { "24h": "24h", "7d": "7d", "30d": "30d", "90d": "90d", "365d": "1y" };

/** "the last 7 days", or "Oct 3 – Oct 9" for a custom window. */
export function windowPhrase(f: InsightFilters): string {
  if (f.start) {
    const fmt = (d: string) => new Date(`${d}T00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    return `${fmt(f.start)} – ${f.end ? fmt(f.end) : "now"}`;
  }
  return `the last ${WINDOW_LABEL[f.window ?? "7d"]}`;
}

/** The comparison window, in words: "the 7 days before". */
export function priorPhrase(start: string, end: string): string {
  const hours = Math.round((Date.parse(end) - Date.parse(start)) / 3_600_000);
  return hours <= 48 ? `the ${hours} hours before` : `the ${Math.round(hours / 24)} days before`;
}

/** A whole percentage, or "—" when there's no denominator. */
export const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v}%`);

/** A delta in points ("+4 pts") or % ("+12%"). */
export function fmtDelta(v: number | null | undefined, unit: "pts" | "%"): string {
  if (v == null) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "±";
  return unit === "pts" ? `${sign}${Math.abs(v)} pts` : `${sign}${Math.abs(v)}%`;
}

/** 83.4 → "1m 23s" */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
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

/** A callout's sessions: its drill filters (nulls dropped) inside the response's scope. */
export function drillHref(ws: string, drill: Record<string, unknown>, scope: Scope): string {
  const f: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(drill)) if (v != null) f[k] = v;
  return sessionsHref(ws, f as SessionFilters, scope);
}

/** One chart bucket's sessions: the bucket replaces the window for the list only. */
export function bucketHref(ws: string, bucket: { start: string; end: string }, scope: Scope, filters: SessionFilters = {}): string {
  return sessionsHref(ws, { ...filters, since: local(bucket.start), until: local(bucket.end) }, scope);
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

/** Rule statuses, with their mark color and meaning. */
export const STATUS_MARK: Record<string, { label: string; color: string; striped?: boolean; meaning: string }> = {
  done: { label: "Done", color: "var(--o-followed)", meaning: "done, in order" },
  ok: { label: "Kept", color: "var(--o-followed)", meaning: "the never rule held" },
  out_of_order: { label: "Out of order", color: "var(--o-partial)", meaning: "done, but out of order" },
  present: { label: "Present", color: "var(--o-partial)", meaning: "the sign came up and was handled" },
  skipped: { label: "Skipped", color: "var(--o-deviated)", meaning: "never done, though the session reached it" },
  violated: { label: "Broken", color: "var(--o-deviated)", meaning: "the agent did what it must never do" },
  unheeded: { label: "Unheeded", color: "var(--o-deviated)", meaning: "the sign came up and the agent didn't act on it" },
  absent: { label: "Absent", color: "var(--o-none)", meaning: "the sign didn't come up" },
  not_reached: { label: "Not reached", color: "var(--o-none)", meaning: "the session ended before it was due" },
  not_applicable: { label: "Not applicable", color: "transparent", striped: true, meaning: "didn't apply; left out of every rate" },
};
export const STATUS_ORDER = ["done", "ok", "out_of_order", "present", "skipped", "violated", "unheeded", "absent", "not_reached", "not_applicable"];

export const OUTCOMES = [
  { key: "followed", label: "Followed", color: "var(--o-followed)", meaning: "every applicable step done in order, no never rule broken" },
  { key: "partial", label: "Partial", color: "var(--o-partial)", meaning: "a step out of order or not reached, or a skip the goal survived" },
  { key: "deviated", label: "Deviated", color: "var(--o-deviated)", meaning: "a step skipped or a never rule broken" },
] as const;
