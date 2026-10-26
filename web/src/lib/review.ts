// Review: verdicts on patterns, overrides on single findings, labels on any rule (corrections and spot checks), and the
// suggestions a verdict leads to.
import { queryOptions } from "@tanstack/react-query";
import type { AgentPromptChange, CalibrateIn, OverrideIn, ReviewExample, ReviewItem, RuleLabelIn, RuleResultOut, SuggestionSummary, VerdictIn } from "@/client/types.gen";
import { call, sdk } from "./api";
import { apiUrl } from "./paths";

export type Verdict = VerdictIn["verdict"];
export const VERDICTS: Verdict[] = ["real", "acceptable", "sop_wrong", "checker_wrong"];

export const VERDICT: Record<Verdict, { label: string; key: string; help: string; tone: string; suggests: boolean }> = {
  real: { label: "Real mistake", key: "1", help: "The agent made a real mistake. Its fix is followed in Fixing, then Fixed.", tone: "bg-err-soft text-err", suggests: true },
  acceptable: { label: "Acceptable", key: "2", help: "Fine in this situation. Closed and hidden from now on. Doesn't go to Fixing or Fixed, even if you edit the rule.", tone: "bg-brand-soft text-primary", suggests: true },
  sop_wrong: { label: "SOP is wrong", key: "3", help: "The instruction is wrong or outdated. Its fix is followed in Fixing, then Fixed.", tone: "bg-warn-soft text-warn", suggests: true },
  checker_wrong: { label: "Checker is wrong", key: "4", help: "sopops misjudged; the agent didn't make this mistake. Closed, hidden, and listed on Fine tune as training data for the checker. Doesn't go to Fixing or Fixed.", tone: "bg-muted text-muted-foreground", suggests: false },
};

export type { RuleCorrectionOut, RuleLabelOut, SpotCheckItem, SpotCheckResponse } from "@/client/types.gen";
export type { CalibrateOut, CalibratedProcedure, CalibratedRule, MergeConflict, OverrideOut as FindingOverride, SinceWritten, ReviewExample, ReviewItem, ReviewResponse, SuggestionDetail, SuggestionSummary, VerdictOut, VerdictSaved } from "@/client/types.gen";
export type SuggestionStatus = SuggestionSummary["status"];
export const SUGGESTION_STATUSES: SuggestionStatus[] = ["proposed", "pr_open", "merged", "applied", "dismissed", "failed"];

/** Where a pattern is on its way from a finding to a fix (`review_state`). */
export type ReviewState = ReviewItem["review_state"];
export const REVIEW_STATES: ReviewState[] = ["needs_review", "regressed", "open", "fixing", "fixed", "closed"];
export const REVIEW_STATE: Record<ReviewState, { label: string; help: string; tone: string }> = {
  needs_review: { label: "Needs review", help: "Nobody has judged it yet, or its findings changed since the last verdict.", tone: "bg-warn-soft text-warn" },
  regressed: { label: "Came back", help: "It was fixed, but the findings came back after the fix went live.", tone: "bg-err-soft text-err ring-1 ring-inset ring-err/50" },
  open: { label: "Open", help: "A real problem with no fix applied yet.", tone: "text-err ring-1 ring-inset ring-err/35" },
  fixing: { label: "Fixing", help: "A fix is applied or in a pull request. Waiting for it to go live and for 10 sessions on it.", tone: "bg-brand-soft text-primary" },
  fixed: { label: "Fixed", help: "The fix is live and 10 sessions since had no findings in it.", tone: "bg-ok/15 text-ok" },
  closed: { label: "Closed", help: "Judged acceptable or a checker mistake. Nothing to fix.", tone: "bg-muted text-muted-foreground" },
};

/** A rule's status, how it reads, and its tone. */
export type RuleStatus = RuleResultOut["status"];
export type RuleKind = RuleResultOut["kind"];
export const RULE_STATUS: Record<RuleStatus, { label: string; tone: string }> = {
  done: { label: "done", tone: "text-ok border-transparent bg-transparent" },
  ok: { label: "ok", tone: "text-ok border-transparent bg-transparent" },
  absent: { label: "absent", tone: "" },
  out_of_order: { label: "out of order", tone: "bg-warn-soft text-warn border-transparent" },
  not_reached: { label: "not reached", tone: "bg-warn-soft text-warn border-transparent" },
  not_applicable: { label: "n/a", tone: "" },
  skipped: { label: "skipped", tone: "bg-err-soft text-err border-transparent" },
  violated: { label: "violated", tone: "bg-err-soft text-err border-transparent" },
  present: { label: "present", tone: "bg-warn-soft text-warn border-transparent" },
  unheeded: { label: "unheeded", tone: "bg-warn-soft text-warn border-transparent font-semibold" },
};
/** The statuses each kind of rule can have, in the order the pickers show them. */
export const KIND_STATUSES: Record<RuleKind, RuleStatus[]> = {
  step: ["done", "out_of_order", "skipped", "not_reached", "not_applicable"],
  never: ["ok", "violated"],
  warning: ["absent", "present", "unheeded"],
};
/** Statuses that are findings: something the agent did wrong. */
export const FINDING_STATUSES: RuleStatus[] = ["skipped", "violated", "out_of_order", "unheeded"];
export const isFinding = (s: RuleStatus) => FINDING_STATUSES.includes(s);
/** What "Checker missed this" offers on a passed rule. */
export const MISSED_STATUSES: Record<RuleKind, RuleStatus[]> = { step: ["skipped", "out_of_order"], never: ["violated"], warning: ["unheeded"] };
/** A status mid-sentence ("corrected to not reached"). */
export const statusPhrase = (s: RuleStatus) => (s === "not_applicable" ? "not applicable" : RULE_STATUS[s].label);

/** The Patterns page's tabs, and the review states each lists. */
export type PatternsTab = "review" | "open" | "fixing" | "fixed" | "all";
export const TAB_STATES: Record<Exclude<PatternsTab, "all">, ReviewState[]> = {
  review: ["needs_review", "regressed"],
  open: ["open"],
  fixing: ["fixing"],
  fixed: ["fixed"],
};
export type PlanAgent = AgentPromptChange;

const path = (workspace: string) => ({ workspace });

export const rq = {
  /** The review queue: patterns in `states` (default: needs review or came back). */
  review: (ws: string, states?: ReviewState[]) =>
    queryOptions({ queryKey: ["ws", ws, "review", states ?? null], queryFn: () => call(sdk.reviewQueue({ path: path(ws), query: { limit: 200, state: states } })) }),
  pattern: (ws: string, id: string) => queryOptions({ queryKey: ["ws", ws, "pattern", id], queryFn: () => call(sdk.getPattern({ path: { ...path(ws), pattern_id: id } })) }),
  suggestions: (ws: string, status?: SuggestionStatus) =>
    queryOptions({ queryKey: ["ws", ws, "suggestions", status ?? null], queryFn: () => call(sdk.listSuggestions({ path: path(ws), query: { status } })) }),
  suggestion: (ws: string, id: string) => queryOptions({ queryKey: ["ws", ws, "suggestion", id], queryFn: () => call(sdk.getSuggestion({ path: { ...path(ws), suggestion_id: id } })) }),
  /** Rule results with no finding, sampled for a reviewer to check, close calls first. */
  /** The current spot-check batch (the first is drawn on the first visit). Filtered and grouped on the page. */
  spotChecks: (ws: string) => queryOptions({ queryKey: ["ws", ws, "spot-checks"], queryFn: () => call(sdk.spotChecks({ path: path(ws) })) }),
};

export const review = {
  /** `keepalive` lets a verdict still waiting out its undo go out as the page closes. */
  verdict: (ws: string, id: string, body: VerdictIn, keepalive = false) =>
    call(sdk.setVerdict({ path: { ...path(ws), pattern_id: id }, body, ...(keepalive ? { keepalive: true } : {}) })),
  override: (ws: string, findingId: string, body: OverrideIn) => call(sdk.overrideFinding({ path: { ...path(ws), finding_id: findingId }, body })),
  /** A new spot-check batch ("give me more"), replacing the current one. */
  drawSpotChecks: (ws: string, size: number) => call(sdk.drawSpotChecks({ path: path(ws), body: { size } })),
  /** A rule's right status: a correction from the session, or a spot-check answer. */
  label: (ws: string, sessionId: string, procedureId: string, ruleId: string, body: RuleLabelIn) =>
    call(sdk.labelRule({ path: { ...path(ws), session_id: sessionId, procedure_id: procedureId, rule_id: ruleId }, body })),
  suggest: (ws: string, id: string) => call(sdk.suggestEdit({ path: { ...path(ws), pattern_id: id } })),
  apply: (ws: string, id: string, reviewer: string | null) => call(sdk.applySuggestion({ path: { ...path(ws), suggestion_id: id }, body: { reviewer } })),
  /** A new suggestion against the procedure as it is now; the old one is dismissed. */
  rewrite: (ws: string, id: string, reviewer: string | null) => call(sdk.rewriteSuggestion({ path: { ...path(ws), suggestion_id: id }, body: { reviewer } })),
  calibrate: (ws: string, body: CalibrateIn) => call(sdk.calibrate({ path: path(ws), body })),
  dismiss: (ws: string, id: string, note: string | null, reviewer: string | null) => call(sdk.dismissSuggestion({ path: { ...path(ws), suggestion_id: id }, body: { note, reviewer } })),
};

export const regressionHref = (ws: string, id: string) => apiUrl(`/v1/workspaces/${encodeURIComponent(ws)}/patterns/${encodeURIComponent(id)}/regression`);

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

/** A verdict's label mid-sentence ("Marked real mistake", "Marked SOP is wrong"). */
export const verdictPhrase = (v: Verdict) => (v === "sop_wrong" ? VERDICT[v].label : VERDICT[v].label.toLowerCase());

/** A verdict that leads to a suggested SOP change. */
export const suggests = (v: Verdict) => VERDICT[v].suggests;

/** Still being written: show a waiting state and poll. */
export const writing = (s: SuggestionSummary | null | undefined) => s?.status === "generating";
