// Insights helpers: URL filters, number formatting (whole %, points), and links into the sessions list.
import { defaultStringifySearch } from "@tanstack/react-router";
import { type InsightFilters, type SessionFilters, WINDOWS } from "./api";
import { wsHref } from "./workspace";

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);
const day = (v: unknown) => (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined);
const oneOf = <T extends string>(v: unknown, options: readonly T[]) => (options.includes(v as T) ? (v as T) : undefined);
/** One or more tags from the URL (`?tag=a&tag=b`). */
export const tagList = (v: unknown): string[] | undefined => {
  const list = (Array.isArray(v) ? v : v == null || v === "" ? [] : [v]).filter((t): t is string => typeof t === "string" && !!t);
  return list.length ? list : undefined;
};
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
    tag: tagList(s.tag),
  };
}

export const WINDOW_LABEL: Record<(typeof WINDOWS)[number], string> = { "24h": "24 hours", "7d": "7 days", "30d": "30 days", "90d": "90 days", "365d": "365 days" };
export const WINDOW_SHORT: Record<(typeof WINDOWS)[number], string> = { "24h": "24h", "7d": "7d", "30d": "30d", "90d": "90d", "365d": "1y" };

/** "the last 7 days", or "Oct 3 to Oct 9" for a custom window. */
export function windowPhrase(f: InsightFilters): string {
  if (f.start) {
    const fmt = (d: string) => new Date(`${d}T00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    return `${fmt(f.start)} to ${f.end ? fmt(f.end) : "now"}`;
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
export type Scope = { start: string; end: string; agent?: string | null; releases?: number[]; channel?: string | null; tags?: string[] };

export function scopeFilters(scope: Scope | undefined): SessionFilters {
  if (!scope) return {};
  return {
    since: local(scope.start),
    until: local(scope.end, 1), // the list's `until` is exclusive and minute-precise
    agent: scope.agent ?? undefined,
    channel: (scope.channel as SessionFilters["channel"]) ?? undefined,
    release: scope.releases?.length === 1 ? scope.releases[0] : undefined,
    tag: scope.tags?.length ? scope.tags : undefined,
  };
}

/** A link to the sessions list with these filters (drop empty ones). Suppressed findings are left out unless the
 * filters say otherwise, so the list's counts match Insights'. */
export function sessionsHref(ws: string, filters: SessionFilters, scope?: Scope): string {
  const all: Record<string, unknown> = { suppressed: false, ...scopeFilters(scope), ...filters };
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

export const KIND_LABEL = { step: "Step", never: "Never rule", warning: "Warning sign" } as const;
export const ruleName = (kind: string | null | undefined, index: number | null | undefined) =>
  kind ? `${KIND_LABEL[kind as keyof typeof KIND_LABEL] ?? kind} ${index ?? ""}`.trim() : "Rule";

/** Statuses that fail a rule, for drill-downs. */
export const FAIL_STATUSES = { step: ["skipped", "out_of_order"], never: ["violated"], warning: ["unheeded"] } as const;

/** Rule statuses, with their mark color and meaning. */
export const STATUS_MARK: Record<string, { label: string; color: string; striped?: boolean; meaning: string }> = {
  done: { label: "Done", color: "var(--o-followed)", meaning: "done, in order" },
  ok: { label: "Kept", color: "var(--o-followed)", meaning: "the agent didn't do what the rule forbids" },
  out_of_order: { label: "Out of order", color: "var(--o-partial)", meaning: "done, but out of order" },
  present: { label: "Present", color: "var(--o-partial)", meaning: "the sign came up and the agent handled it" },
  skipped: { label: "Skipped", color: "var(--o-deviated)", meaning: "not done, though the session got that far" },
  violated: { label: "Broken", color: "var(--o-deviated)", meaning: "the agent did what the rule forbids" },
  unheeded: { label: "Unheeded", color: "var(--o-deviated)", meaning: "the sign came up and the agent didn't act on it" },
  absent: { label: "Absent", color: "var(--o-none)", meaning: "the sign didn't come up" },
  not_reached: { label: "Not reached", color: "var(--o-none)", meaning: "the session ended before this point" },
  not_applicable: { label: "Not applicable", color: "transparent", striped: true, meaning: "didn't apply to the session; left out of every rate" },
};
export const STATUS_ORDER = ["done", "ok", "out_of_order", "present", "skipped", "violated", "unheeded", "absent", "not_reached", "not_applicable"];

export const OUTCOMES = [
  {
    key: "followed",
    label: "Followed",
    color: "var(--o-followed)",
    meaning: "every step that applied done in order, no never rule broken",
    tip: "The agent did every step that applied, in order, and broke no never rule. Only followed checks count as a pass.",
  },
  {
    key: "partial",
    label: "Partial",
    color: "var(--o-partial)",
    meaning: "a step out of order or not reached, or a skipped step the goal survived",
    tip: "A step came out of order or the session ended before it, or a step was skipped and the session still reached its goal. Partial counts against the pass rate.",
  },
  {
    key: "deviated",
    label: "Deviated",
    color: "var(--o-deviated)",
    meaning: "a step skipped or a never rule broken",
    tip: "The agent skipped a step or broke a never rule. These are the sessions to read first.",
  },
] as const;

/** One sentence that defines a check, for hovers that count them. */
export const CHECK_DEF = "A check is one session judged against one procedure.";

/** Rule statuses with a hover each, for legends. */
export const statusTip = (k: string) => {
  const m = STATUS_MARK[k];
  return m ? `${m.meaning.charAt(0).toUpperCase()}${m.meaning.slice(1)}.` : undefined;
};

// --- release compare: the same tests the server applies, for pooled numbers it doesn't return ---------------------

function wilsonRaw(passes: number, n: number, z = 1.96): [number, number] {
  const p = passes / n;
  const denom = 1 + (z * z) / n;
  const centre = (p + (z * z) / (2 * n)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / denom;
  return [Math.max(0, centre - half), Math.min(1, centre + half)];
}

/** 95% Wilson interval in whole percent, as the server rounds it. */
export function wilson(passes: number, n: number): [number, number] | null {
  if (!n) return null;
  const [lo, hi] = wilsonRaw(passes, n);
  return [Math.round(lo * 100), Math.round(hi * 100)];
}

/** 95% interval for B's rate minus A's, in whole points (Newcombe's method, from both Wilson intervals). */
export function diffInterval(aPass: number, aN: number, bPass: number, bN: number): [number, number] | null {
  if (!aN || !bN) return null;
  const pa = aPass / aN;
  const pb = bPass / bN;
  const [la, ua] = wilsonRaw(aPass, aN);
  const [lb, ub] = wilsonRaw(bPass, bN);
  const d = pb - pa;
  return [Math.round((d - Math.sqrt((pb - lb) ** 2 + (ua - pa) ** 2)) * 100), Math.round((d + Math.sqrt((ub - pb) ** 2 + (pa - la) ** 2)) * 100)];
}

/** thin | regressed | improved | steady for B against A: the server's `compare_flag`. */
export function compareFlag(aPass: number, aN: number, bPass: number, bN: number, minN: number, points: number): "thin" | "regressed" | "improved" | "steady" {
  if (aN < minN || bN < minN) return "thin";
  const ia = wilson(aPass, aN);
  const ib = wilson(bPass, bN);
  const d = Math.round((100 * bPass) / bN) - Math.round((100 * aPass) / aN);
  const apart = !!ia && !!ib && (ib[1] < ia[0] || ib[0] > ia[1]);
  if (d <= -points && apart) return "regressed";
  if (d >= points && apart) return "improved";
  return "steady";
}

/** A signed whole number of points: "+4", "−3", "0". */
export const signed = (v: number) => (v > 0 ? `+${v}` : v < 0 ? `−${Math.abs(v)}` : "0");

/** The two sides of a release comparison: one letter, one color, one word each, everywhere on the page. */
export const SIDES = {
  a: { letter: "A", word: "baseline", color: "var(--side-a)", badge: "bg-side-a text-white dark:text-background" },
  b: { letter: "B", word: "candidate", color: "var(--side-b)", badge: "bg-side-b text-white dark:text-background" },
} as const;
