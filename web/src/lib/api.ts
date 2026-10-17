// The generated client (src/client, from FastAPI's OpenAPI spec), errors as ApiError, and the queries pages share.
import { type QueryClient, queryOptions } from "@tanstack/react-query";
import { client } from "@/client/client.gen";
import * as sdk from "@/client/sdk.gen";
import type { ConfigResponse, IssueOut } from "@/client/types.gen";

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
  release: (ws: string, number: number) =>
    queryOptions({ queryKey: ["ws", ws, "release", number], queryFn: () => call(sdk.getRelease({ path: { workspace: ws, number } })), staleTime: Infinity }),
};

/** After a write: refetch everything about the workspace (and the workspace list, which a first write adds to). */
export async function refresh(qc: QueryClient, ws: string) {
  await Promise.all([qc.invalidateQueries({ queryKey: ["ws", ws] }), qc.invalidateQueries({ queryKey: ["workspaces"] })]);
}
