// The generated client (src/client, from FastAPI's OpenAPI spec), errors as ApiError, and the queries pages share.
import { type QueryClient, infiniteQueryOptions, queryOptions } from "@tanstack/react-query";
import { client } from "@/client/client.gen";
import * as sdk from "@/client/sdk.gen";
import type { ConfigResponse, IssueOut, RuleResultOut, SessionSummary } from "@/client/types.gen";

client.setConfig({ baseUrl: "" });

export { sdk };
export type * from "@/client/types.gen";

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(message: string, status: number, data: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
  /** sopc's issues, for a 422 from a write that doesn't compile. */
  get issues(): IssueOut[] {
    const d = this.data as { issues?: IssueOut[] } | null;
    return (d && Array.isArray(d.issues) && d.issues) || [];
  }
}

function errorMessage(status: number, data: unknown): string {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail.charAt(0).toUpperCase() + detail.slice(1) + ".";
  if (Array.isArray(detail)) return "Some values are missing or have the wrong type.";
  return `The server answered ${status}.`;
}

/** Await a generated-client call: its data, or an ApiError. */
export async function call<T>(p: Promise<{ data?: T; error?: unknown; response?: Response }>): Promise<T> {
  const r = await p;
  const status = r.response?.status ?? 0;
  if (r.error !== undefined || !r.response?.ok) throw new ApiError(errorMessage(status, r.error), status, r.error ?? null);
  return r.data as T;
}

/** An issue made here rather than by sopc (a message to show the same way). */
export const localIssue = (text: string, field: string | null = null): IssueOut => ({
  code: "",
  message: text,
  path: "",
  severity: "error",
  kind: null,
  id: null,
  field,
  index: null,
  text,
});

export const issuesOf = (e: unknown): IssueOut[] => (e instanceof ApiError ? e.issues : []);

// Issues that mean a workspace is stored in an older sopc format, which the Convert page fixes.
export const LEGACY_CODES = ["old_format", "removed_field"] as const;
export type Legacy = (typeof LEGACY_CODES)[number] | null;

export const EMPTY_CONFIG = (workspace: string): ConfigResponse => ({
  workspace,
  settings: { variables: {}, procedures_heading: "## Procedures", file: "sopc.yaml", version: null, updated_at: null, updated_by: null },
  agents: [],
  instructions: [],
  procedures: [],
  groups: [],
});

const path = (workspace: string) => ({ path: { workspace } });

export const q = {
  workspaces: () => queryOptions({ queryKey: ["workspaces"], queryFn: () => call(sdk.listWorkspaces()) }),
  config: (ws: string) =>
    queryOptions({
      queryKey: ["ws", ws, "config"],
      queryFn: async (): Promise<{ cfg: ConfigResponse; legacy: Legacy }> => {
        try {
          return { cfg: await call(sdk.getConfig(path(ws))), legacy: null };
        } catch (e) {
          const codes = issuesOf(e).map((i) => i.code);
          const legacy = e instanceof ApiError && e.status === 422 ? (LEGACY_CODES.find((c) => codes.includes(c)) ?? null) : null;
          if (legacy) return { cfg: EMPTY_CONFIG(ws), legacy };
          throw e;
        }
      },
    }),
  draft: (ws: string) => queryOptions({ queryKey: ["ws", ws, "draft"], queryFn: () => call(sdk.draft(path(ws))) }),
  releases: (ws: string) => queryOptions({ queryKey: ["ws", ws, "releases"], queryFn: () => call(sdk.listReleases(path(ws))) }),
  sessions: (ws: string, filters: SessionFilters) =>
    infiniteQueryOptions({
      queryKey: ["ws", ws, "sessions", filters],
      queryFn: ({ pageParam }) =>
        call(sdk.listSessions({ path: { workspace: ws }, query: { ...sessionQuery(filters), cursor: pageParam ?? undefined, limit: 50 } })),
      initialPageParam: null as string | null,
      getNextPageParam: (last) => last.next_cursor,
    }),
  session: (ws: string, id: string) => queryOptions({ queryKey: ["ws", ws, "session", id], queryFn: () => call(sdk.getSession({ path: { workspace: ws, id } })) }),
  tokens: (ws: string) => queryOptions({ queryKey: ["ws", ws, "tokens"], queryFn: () => call(sdk.listTokens(path(ws))) }),
  release: (ws: string, number: number) =>
    queryOptions({ queryKey: ["ws", ws, "release", number], queryFn: () => call(sdk.getRelease({ path: { workspace: ws, number } })), staleTime: Infinity }),
  insightsOverview: (ws: string, f: InsightFilters) =>
    queryOptions({ queryKey: ["ws", ws, "insights", "overview", f], queryFn: () => call(sdk.insightsOverview({ path: { workspace: ws }, query: insightQuery(f) })) }),
  insightsProcedure: (ws: string, id: string, f: InsightFilters) =>
    queryOptions({
      queryKey: ["ws", ws, "insights", "procedure", id, f],
      queryFn: () => call(sdk.insightsProcedure({ path: { workspace: ws, procedure_id: id }, query: insightQuery({ ...f, procedure: undefined }) })),
    }),
  insightsRule: (ws: string, procedureId: string, ruleId: string, f: InsightFilters) =>
    queryOptions({
      queryKey: ["ws", ws, "insights", "rule", procedureId, ruleId, f],
      queryFn: () => call(sdk.insightsRule({ path: { workspace: ws, procedure_id: procedureId, rule_id: ruleId }, query: insightQuery({ ...f, procedure: undefined }) })),
    }),
  insightsCompare: (ws: string, f: InsightFilters, a?: number, b?: number) =>
    queryOptions({
      queryKey: ["ws", ws, "insights", "compare", f, a, b],
      queryFn: () => call(sdk.insightsCompare({ path: { workspace: ws }, query: { ...insightQuery({ ...f, release: undefined }), a, b } })),
    }),
  insightsReport: (ws: string, f: InsightFilters) =>
    queryOptions({ queryKey: ["ws", ws, "insights", "report", f], queryFn: () => call(sdk.insightsReport({ path: { workspace: ws }, query: insightQuery(f) })) }),
  patterns: (ws: string, procedure?: string) =>
    queryOptions({ queryKey: ["ws", ws, "patterns", procedure], queryFn: () => call(sdk.listPatterns({ path: { workspace: ws }, query: { procedure } })) }),
  pattern: (ws: string, id: string) =>
    queryOptions({ queryKey: ["ws", ws, "pattern", id], queryFn: () => call(sdk.getPattern({ path: { workspace: ws, pattern_id: id } })) }),
};

/** The Insights pages' filters, as kept in the URL. */
export const WINDOWS = ["24h", "7d", "30d", "90d", "365d"] as const;
export type InsightWindow = (typeof WINDOWS)[number];
export type InsightFilters = {
  window?: InsightWindow;
  agent?: string;
  procedure?: string;
  release?: number[];
  channel?: "voice" | "text";
};

function insightQuery(f: InsightFilters) {
  return { window: f.window ?? "7d", agent: f.agent, procedure: f.procedure, release: f.release?.length ? f.release : undefined, channel: f.channel };
}

/** After a write: refetch everything about the workspace (and the workspace list, which a first write adds to). */
export async function refresh(qc: QueryClient, ws: string) {
  await Promise.all([qc.invalidateQueries({ queryKey: ["ws", ws] }), qc.invalidateQueries({ queryKey: ["workspaces"] })]);
}

/** The Sessions page's filters, as kept in the URL. `since`/`until` are local date-times (datetime-local inputs). */
export type SessionFilters = {
  agent?: string;
  channel?: "voice" | "text";
  status?: SessionSummary["status"];
  result?: NonNullable<SessionSummary["result"]>;
  since?: string;
  until?: string;
  // drill-down filters, set by links from Insights and the report
  release?: number;
  procedure?: string;
  procedure_result?: NonNullable<SessionSummary["result"]>;
  rule?: string;
  rule_status?: RuleResultOut["status"][];
  pattern?: string;
  theme?: string;
  finding?: boolean;
  eligible?: boolean;
};

function sessionQuery(f: SessionFilters) {
  const iso = (local?: string) => (local ? new Date(local).toISOString() : undefined);
  return {
    agent: f.agent,
    channel: f.channel,
    status: f.status,
    result: f.result,
    since: iso(f.since),
    until: iso(f.until),
    release: f.release,
    procedure: f.procedure,
    procedure_result: f.procedure_result,
    rule: f.rule,
    rule_status: f.rule_status?.length ? f.rule_status : undefined,
    pattern: f.pattern,
    theme: f.theme,
    finding: f.finding,
    eligible: f.eligible,
  };
}

/** What `…/sessions/stream` sends with each `session` event. */
export type SessionEvent = { type: "created" | "updated"; workspace: string; id: string; status: SessionSummary["status"] };
