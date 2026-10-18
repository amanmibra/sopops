// Review: verdicts on patterns, overrides on single findings, and the suggestions a verdict leads to.
import { queryOptions } from "@tanstack/react-query";
import type { AgentPromptChange, OverrideIn, ReviewExample, SuggestionSummary, VerdictIn } from "@/client/types.gen";
import { call, sdk } from "./api";

export type Verdict = VerdictIn["verdict"];
export const VERDICTS: Verdict[] = ["real", "acceptable", "sop_wrong", "checker_wrong"];

export const VERDICT: Record<Verdict, { label: string; key: string; help: string; tone: string; suggests: boolean }> = {
  real: { label: "Real mistake", key: "1", help: "The agent made a real mistake.", tone: "bg-err-soft text-err", suggests: true },
  acceptable: { label: "Acceptable", key: "2", help: "Fine in this situation. Hidden from now on.", tone: "bg-brand-soft text-primary", suggests: true },
  sop_wrong: { label: "SOP is wrong", key: "3", help: "The instruction is wrong or outdated.", tone: "bg-warn-soft text-warn", suggests: true },
  checker_wrong: { label: "Checker is wrong", key: "4", help: "sopops misjudged. Hidden, and used to recalibrate.", tone: "bg-muted text-muted-foreground", suggests: false },
};

export type { OverrideOut as FindingOverride, ReviewExample, ReviewItem, ReviewResponse, SuggestionDetail, SuggestionSummary, VerdictOut, VerdictSaved } from "@/client/types.gen";
export type SuggestionStatus = SuggestionSummary["status"];
export const SUGGESTION_STATUSES: SuggestionStatus[] = ["proposed", "pr_open", "applied", "dismissed", "failed"];
export type PlanAgent = AgentPromptChange;

const path = (workspace: string) => ({ workspace });

export const rq = {
  review: (ws: string) => queryOptions({ queryKey: ["ws", ws, "review"], queryFn: () => call(sdk.reviewQueue({ path: path(ws), query: { limit: 200 } })) }),
  pattern: (ws: string, id: string) => queryOptions({ queryKey: ["ws", ws, "pattern", id], queryFn: () => call(sdk.getPattern({ path: { ...path(ws), pattern_id: id } })) }),
  suggestions: (ws: string, status?: SuggestionStatus) =>
    queryOptions({ queryKey: ["ws", ws, "suggestions", status ?? null], queryFn: () => call(sdk.listSuggestions({ path: path(ws), query: { status } })) }),
  suggestion: (ws: string, id: string) => queryOptions({ queryKey: ["ws", ws, "suggestion", id], queryFn: () => call(sdk.getSuggestion({ path: { ...path(ws), suggestion_id: id } })) }),
};

export const review = {
  /** `keepalive` lets a verdict still waiting out its undo go out as the page closes. */
  verdict: (ws: string, id: string, body: VerdictIn, keepalive = false) =>
    call(sdk.setVerdict({ path: { ...path(ws), pattern_id: id }, body, ...(keepalive ? { keepalive: true } : {}) })),
  override: (ws: string, findingId: string, body: OverrideIn) => call(sdk.overrideFinding({ path: { ...path(ws), finding_id: findingId }, body })),
  suggest: (ws: string, id: string) => call(sdk.suggestEdit({ path: { ...path(ws), pattern_id: id } })),
  apply: (ws: string, id: string, reviewer: string | null) => call(sdk.applySuggestion({ path: { ...path(ws), suggestion_id: id }, body: { reviewer } })),
  dismiss: (ws: string, id: string, note: string | null, reviewer: string | null) => call(sdk.dismissSuggestion({ path: { ...path(ws), suggestion_id: id }, body: { note, reviewer } })),
};

export const regressionHref = (ws: string, id: string) => `/v1/workspaces/${encodeURIComponent(ws)}/patterns/${encodeURIComponent(id)}/regression`;

/** A suggestion's one-line summary: the first sentence of its rationale. */
export const summaryOf = (s: SuggestionSummary) => s.rationale?.split(/(?<=[.!?])\s/)[0] ?? null;

/** An example finding as the pages show it, from the review queue or a pattern's examples. */
export type Ex = { key: string; session_id: string; started_at: string | null; turn: number | null; excerpt: string | null; reason?: string | null; suppressed?: boolean; finding_id?: string };
export const exOf = (e: ReviewExample): Ex => ({ key: e.finding_id, finding_id: e.finding_id, session_id: e.session_id, started_at: e.started_at, turn: turnOf(e), excerpt: e.excerpt, reason: e.reason, suppressed: e.suppressed });

/** The turn a finding's quote is on. */
export const turnOf = (e: Pick<ReviewExample, "evidence">): number | null => {
  const t = e.evidence.find((x) => typeof x.turn === "number")?.turn;
  return typeof t === "number" ? t : null;
};

/** A verdict that leads to a suggested SOP change. */
export const suggests = (v: Verdict) => VERDICT[v].suggests;

/** Still being written: show a waiting state and poll. */
export const writing = (s: SuggestionSummary | null | undefined) => s?.status === "generating";
