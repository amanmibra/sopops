// Patterns: every cluster (themes, rule patterns, the patterns they split into) in tabs by review state (needs review, open,
// fixing, fixed, all), one pattern's page with where its fix stands, and the
// "wrong?" control on a single finding.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Download, Keyboard, Loader2, RefreshCw, RotateCcw, Sparkles, Undo2, X, Check } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { type PatternOut, type RulePatternOut, type RuleRef, call, q, refresh, sdk } from "@/lib/api";
import { ruleName, sessionsHref } from "@/lib/insights";
import { plural, when } from "@/lib/kinds";
import {
  ASK,
  type Answer,
  type Ex,
  type OrderStep,
  type PatternsTab,
  type ReviewItem,
  type SuggestionSummary,
  TAB_STATES,
  VERDICT,
  VERDICTS,
  type Verdict,
  type VerdictOut,
  regressionHref,
  review,
  rq,
  verdictPhrase,
  suggests,
  summaryOf,
  exOf,
  ruleWord,
  writing,
} from "@/lib/review";
import { prefs } from "@/lib/prefs";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ErrorBanner, Note, PageTitle, RuleText, Sub, Tag } from "./bits";
import { author } from "./form";
import { HrefLink } from "./href-link";
import { Hint, Skel, TipBody } from "./insights/kit";
import { Page } from "./item-shell";
import { AskThenWrite, ConflictTag, ReviewStateChip, RewordedNote, RuleTag, SuggestionState, SuggestionStatusTag, VerdictChip, ago, usePatternIndex } from "./review-bits";
import { FixTimeline, StateList } from "./review-states";
import { FilterSelect } from "./sessions";
import { Button } from "./ui/button";
import { Tabs, TabsList, TabsTrigger } from "./ui/tabs";
import { Textarea } from "./ui/textarea";


/** The verdict legend starts open, and stays folded in this browser once someone gives a verdict or folds it. */
const verdictLegend = {
  open: () => prefs.get("verdict-legend") !== "closed",
  close: () => prefs.set("verdict-legend", "closed"),
  reopen: () => prefs.remove("verdict-legend"),
};

export { SUGGESTION_STATUS, ReviewStateChip, SuggestionStatusTag, VerdictChip, ago } from "./review-bits";

// --- small pieces --------------------------------------------------------------------------------------------------

/** What each answer to the two questions leads to: the verdict it gives and what follows. */
export function VerdictLegend({ className }: { className?: string }) {
  const rule = ASK.rule("step");
  const row = (keys: string, answer: string, v?: Verdict, sub?: boolean) => (
    <div key={keys} className={cn("flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1", sub && "pl-6")}>
      <dt className="shrink-0">
        <kbd className="text-muted-foreground mr-2 rounded border px-1 font-mono text-sm">{keys.split(" ").pop()}</kbd>
        <span className="font-medium">{answer}</span>
        {v && <span className={cn("ml-2 rounded-full px-2 py-px text-xs font-medium", VERDICT[v].tone)}>{VERDICT[v].label}</span>}
      </dt>
      <dd className="text-muted-foreground min-w-0">{v ? VERDICT[v].help : `Then: ${rule.question.replace(/^Is/, "is")}`}</dd>
    </div>
  );
  return (
    <div className={cn("grid gap-3 text-sm", className)}>
      <p className="font-semibold">{ASK.problem.question}</p>
      <dl className="grid gap-y-3">
        {row("1", "Yes")}
        {rule.answers.map((a) => row(`1 ${a.key}`, a.label, a.verdict, true))}
        {ASK.problem.answers.filter((a) => a.verdict).map((a) => row(a.key, a.label, a.verdict))}
      </dl>
      <p className="text-muted-foreground">
        <kbd className="mr-1 rounded border px-1 font-mono text-sm">n</kbd> Add a note with any answer. It's kept with the verdict, given to sopops when it drafts the SOP edit, and kept with
        the training data.
      </p>
    </div>
  );
}

/** The verdict, asked as two questions (`ASK`): is this a problem, then whether the SOP's rule is right. Answer keys
 * go through `keysRef` (the queue's keyboard); the hint under the buttons says what the hovered answer does. */
function VerdictPicker({
  current,
  busy,
  onPick,
  noteRef,
  keysRef,
  ruleKind,
}: {
  current?: Verdict | null;
  busy?: boolean;
  onPick: (v: Verdict, note: string) => void;
  noteRef?: React.RefObject<HTMLTextAreaElement | null>;
  keysRef?: React.RefObject<((key: string) => boolean) | null>;
  ruleKind?: string | null;
}) {
  const [note, setNote] = useState("");
  const [noting, setNoting] = useState(false);
  const [second, setSecond] = useState(false);
  const [hover, setHover] = useState<Answer | null>(null);
  const local = useRef<HTMLTextAreaElement>(null);
  const ref = noteRef ?? local;
  const word = ruleWord(ruleKind);
  const ask = second ? ASK.rule(word) : ASK.problem;
  const answer = (a: Answer) => {
    setHover(null);
    if (a.next) return setSecond(true);
    if (!a.verdict) return;
    onPick(a.verdict, note.trim());
    setNote("");
    setNoting(false);
    setSecond(false);
  };
  if (keysRef)
    keysRef.current = (key) => {
      const a = ask.answers.find((x) => x.key === key);
      if (a) answer(a);
      else if (key === "Escape" && second) setSecond(false);
      else return false;
      return true;
    };
  const chosen = (a: Answer) => (a.verdict ? current === a.verdict : current === "real" || current === "sop_wrong");
  const hint = hover
    ? hover.verdict
      ? <><span className="text-foreground font-medium">{VERDICT[hover.verdict].label}.</span> {VERDICT[hover.verdict].help.replace(/\bstep\b/g, word)}</>
      : `Then: is the SOP ${word} right? Either answer has sopops draft an edit to the SOP for you to review.`
    : second
      ? "Either way, sopops drafts an edit to the SOP for you to review."
      : "Not sure? Open an example first.";
  return (
    <div className="grid gap-2">
      {second && (
        <p className="text-muted-foreground text-xs">
          {ASK.problem.question} <span className="text-foreground font-medium">Yes</span> ·{" "}
          <button type="button" className="text-primary underline underline-offset-2" onClick={() => setSecond(false)}>
            change
          </button>
        </p>
      )}
      <p className="text-sm font-semibold">{ask.question}</p>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-2 sm:flex sm:flex-wrap sm:items-center">
        {ask.answers.map((a) => (
          <Button
            key={a.label}
            size="sm"
            variant="outline"
            disabled={busy}
            aria-pressed={chosen(a)}
            title={a.verdict ? VERDICT[a.verdict].help : undefined}
            className={cn("justify-start", chosen(a) && "bg-brand-soft border-primary/40")}
            onMouseEnter={() => setHover(a)}
            onMouseLeave={() => setHover(null)}
            onFocus={() => setHover(a)}
            onBlur={() => setHover(null)}
            onClick={() => answer(a)}
          >
            <kbd className="text-muted-foreground rounded border px-1 font-mono text-sm leading-4">{a.key}</kbd>
            {a.label}
          </Button>
        ))}
        {!noting && (
          <Button size="sm" variant="ghost" className="text-muted-foreground justify-start" onClick={() => setNoting(true)}>
            <kbd className="rounded border px-1 font-mono text-sm leading-4">n</kbd> Add a note
          </Button>
        )}
      </div>
      <p className="text-muted-foreground min-h-[1.25rem] text-xs">{hint}</p>
      {(noting || note) && (
        <Textarea
          ref={ref}
          autoFocus={noting}
          className="min-h-12 text-sm"
          placeholder="Why? (optional; kept with the verdict, given to sopops when it drafts the SOP edit, and kept with the training data)"
          aria-label="Note for the verdict"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") (e.target as HTMLElement).blur();
          }}
        />
      )}
      {/* `n` from the queue opens the note field even before it's shown */}
      {!noting && !note && <textarea ref={ref} className="sr-only" tabIndex={-1} aria-hidden onFocus={() => setNoting(true)} />}
    </div>
  );
}

export function sessionAt(ws: string, ex: Pick<Ex, "session_id" | "turn" | "finding_id">, procedureId: string, ruleId: string) {
  const qs = new URLSearchParams({ procedure: procedureId, rule: ruleId });
  if (ex.turn != null) qs.set("turn", String(ex.turn));
  if (ex.finding_id) qs.set("finding", ex.finding_id);
  return `${wsHref(ws, "sessions", ex.session_id)}?${qs}`;
}

/** Examples as links to their sessions. `first`: show one and fold the rest under "N more". The checker's reason is
 * shown only where the examples' reasons differ: in one pattern they usually say the same thing as its name. */
function Examples({ items, procedureId, ruleId, first }: { items: Ex[]; procedureId: string; ruleId: string; first?: boolean }) {
  const { ws } = useWs();
  if (!items.length) return <Note>No examples in the window.</Note>;
  const shown = items.slice(0, 5);
  const reasons = new Set(shown.map((e) => e.reason ?? ""));
  const quotes = shown.some((e) => e.excerpt);
  const row = (e: Ex) => (
    <li key={e.key} className="text-sm">
      <HrefLink href={sessionAt(ws, e, procedureId, ruleId)} className="text-foreground hover:bg-muted flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-md border px-2 py-1.5 hover:no-underline" title="Open the session at this finding">
        <span className="text-muted-foreground text-xs whitespace-nowrap">{e.started_at ? when(e.started_at) : "Session"}</span>
        {e.excerpt ? <span className="min-w-0">“{e.excerpt}”</span> : <span className="text-muted-foreground min-w-0">{e.tools ? "In the tool calls, no quote" : "No quote"}</span>}
        {e.suppressed && <Tag className="align-middle">suppressed</Tag>}
        <span className="text-primary ml-auto text-xs whitespace-nowrap">Open session →</span>
        {reasons.size > 1 && e.reason && <span className="text-muted-foreground basis-full text-xs">{e.reason}</span>}
      </HrefLink>
    </li>
  );
  if (!first) return <ul className="grid grid-cols-[minmax(0,1fr)] gap-2">{shown.map(row)}</ul>;
  const [lead, ...rest] = shown;
  return (
    <div className="grid gap-1.5">
      <ul className="grid grid-cols-[minmax(0,1fr)]">{row(lead)}</ul>
      {rest.length > 0 && (
        <details className="group">
          <summary className="text-primary flex cursor-pointer list-none items-center gap-1 text-xs [&::-webkit-details-marker]:hidden">
            <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
            {rest.length} more{!quotes && reasons.size <= 1 ? ", all the same" : ""}
          </summary>
          <ul className="mt-1.5 grid grid-cols-[minmax(0,1fr)] gap-1.5">{rest.map(row)}</ul>
        </details>
      )}
    </div>
  );
}

/** An out-of-order example's two swapped steps: `late`, the step it's about (the finding), and `early`, the one the agent
 * did out of turn against it (nearest in the procedure); `hi` is the later of the two in the procedure, which ran first. */
function swapOf(order: OrderStep[] | null | undefined, index: number | null | undefined) {
  if (!order || index == null) return null;
  const late = order.find((s) => s.index === index && s.turn != null);
  if (!late || late.turn == null) return null;
  const t = late.turn;
  const against = order
    .filter((s) => s.index !== index && s.turn != null && (s.status === "done" || s.status === "out_of_order") && (s.index > index ? s.turn! < t : s.turn! > t))
    .sort((a, b) => Math.abs(a.index - index) - Math.abs(b.index - index))[0];
  if (!against) return null;
  const [lo, hi] = against.index < index ? [against, late] : [late, against];
  const before = order.find((s) => s.index === lo.index - 1 && s.turn != null && s.turn < Math.min(lo.turn!, hi.turn!));
  return { lo, hi, before, done: order.filter((s) => s.turn != null && (s.status === "done" || s.status === "out_of_order")).sort((a, b) => a.turn! - b.turn! || a.index - b.index) };
}

/** A step as one line: its number and text, cut to fit, with the whole text on hover. */
function StepPill({ s, hit, className }: { s: OrderStep; hit?: boolean; className?: string }) {
  return (
    <span title={`Step ${s.index}: ${s.text}`} className={cn("min-w-0 truncate border px-2 text-sm", hit ? "border-err bg-err-soft text-err" : "border-input", className)}>
      <b className="font-semibold">{s.index}</b> {s.text}
    </span>
  );
}

/** The swap at a glance: at most three steps a row, the procedure's order over the agent's. On a phone, only the two
 * that swapped. */
function OrderStrip({ order, index, more }: { order: OrderStep[] | null | undefined; index: number | null | undefined; more?: string }) {
  const swap = swapOf(order, index);
  if (!swap) return null;
  const { lo, hi, before } = swap;
  const cols = before ? "grid-cols-[3rem_repeat(2,minmax(0,1fr))] sm:grid-cols-[3rem_repeat(3,minmax(0,1fr))]" : "grid-cols-[3rem_repeat(2,minmax(0,1fr))]";
  const ctx = before && <StepPill s={before} className="hidden sm:block" />;
  return (
    <div className="grid gap-1">
      <div className={cn("grid items-center gap-x-1.5 gap-y-1", cols)}>
        <span className="text-muted-foreground text-xs">SOP</span>
        {ctx}
        <StepPill s={lo} />
        <StepPill s={hi} />
        <span className="text-muted-foreground text-xs">Agent</span>
        {ctx}
        <StepPill s={hi} hit />
        <StepPill s={lo} />
      </div>
      <p className="text-muted-foreground text-xs">
        Step {hi.index} ran before step {lo.index}.
        {more && (
          <>
            {" "}
            <HrefLink href={more}>See every step</HrefLink>
          </>
        )}
      </p>
    </div>
  );
}

/** Every step: the procedure's order beside the agent's in one session, with the swapped step marked. */
function FullOrder({ order, index }: { order: OrderStep[]; index: number | null | undefined }) {
  const swap = swapOf(order, index);
  const steps = [...order].sort((a, b) => a.index - b.index);
  const missing = steps.filter((s) => s.turn == null || !(s.status === "done" || s.status === "out_of_order"));
  const li = (s: OrderStep, hit: boolean) => (
    <li key={s.index} className={cn("grid grid-cols-[1.5rem_minmax(0,1fr)] gap-2 px-2 py-1 text-sm", hit ? "bg-err-soft text-err" : s.index === index && "bg-muted")}>
      <span className={hit ? "" : "text-muted-foreground"}>{s.index}</span>
      <span>{s.text}</span>
    </li>
  );
  return (
    <div className="grid gap-3">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="min-w-0">
          <h3 className="text-muted-foreground mb-1 text-xs font-medium">The SOP</h3>
          <ol className="grid gap-0.5">{steps.map((s) => li(s, false))}</ol>
        </div>
        <div className="min-w-0">
          <h3 className="text-muted-foreground mb-1 text-xs font-medium">What the agent did</h3>
          <ol className="grid gap-0.5">{(swap?.done ?? []).map((s) => li(s, s.index === swap?.hi.index))}</ol>
        </div>
      </div>
      {swap && <p className="text-muted-foreground text-xs">Step {swap.hi.index} ran before step {swap.lo.index}.{missing.length > 0 && ` Not done in this session: ${missing.map((s) => s.index).join(", ")}.`}</p>}
    </div>
  );
}

/** What a pattern is about, each fact labelled: its procedure, what the SOP says, the order (out-of-order patterns) and
 * an example. */
function PatternFacts({
  proc,
  procHref,
  theme,
  rule,
  examples,
  procedureId,
  ruleId,
  orderHref,
}: {
  proc: string;
  procHref?: string;
  theme?: string | null;
  rule: RuleRef | null | undefined;
  examples?: Ex[];
  procedureId: string;
  ruleId: string;
  orderHref?: string;
}) {
  const ordered = examples?.find((e) => e.order);
  return (
    <dl className="grid grid-cols-[minmax(0,1fr)] gap-x-5 gap-y-1 text-sm sm:grid-cols-[max-content_minmax(0,1fr)] sm:gap-y-3">
      <dt className="text-muted-foreground text-xs sm:text-sm">Procedure</dt>
      <dd className="mb-2 min-w-0 sm:mb-0">
        {procHref ? (
          <HrefLink href={procHref} className="text-k-procedure font-medium">
            {proc}
          </HrefLink>
        ) : (
          <span className="text-k-procedure font-medium">{proc}</span>
        )}
        {theme && <span className="text-muted-foreground"> › {theme}</span>}
      </dd>
      {rule?.text && (
        <>
          <dt className="text-muted-foreground text-xs sm:text-sm">SOP says</dt>
          <dd className="border-k-procedure/50 mb-2 min-w-0 border-l-2 pl-2 sm:mb-0">
            <span className="text-muted-foreground">{ruleName(rule.kind, rule.index)}:</span> <RuleText text={rule.text} />
          </dd>
        </>
      )}
      {ordered && orderHref && swapOf(ordered.order, rule?.index) && (
        <>
          <dt className="text-muted-foreground text-xs sm:text-sm">Order</dt>
          <dd className="mb-2 min-w-0 sm:mb-0">
            <OrderStrip order={ordered.order} index={rule?.index} more={orderHref} />
          </dd>
        </>
      )}
      {examples && (
        <>
          <dt className="text-muted-foreground text-xs sm:text-sm">Example</dt>
          <dd className="min-w-0">
            <Examples items={examples} procedureId={procedureId} ruleId={ruleId} first />
          </dd>
        </>
      )}
    </dl>
  );
}

// --- the review card -----------------------------------------------------------------------------------------------

type Decision = { verdict: Verdict; note: string; phase: "waiting" | "sending" | "sent" | "error"; suggestion?: SuggestionSummary | null; error?: unknown };

/** The pattern you're on in the queue: what happened, the labelled facts and the two questions. The others are a row. */
function ReviewCard({
  item,
  focused,
  decision,
  onFocus,
  onPick,
  onUndo,
  onReopen,
  noteRef,
  keysRef,
}: {
  item: ReviewItem;
  focused: boolean;
  decision?: Decision;
  onFocus: () => void;
  onPick: (v: Verdict, note: string) => void;
  onUndo: () => void;
  onReopen: () => void;
  noteRef: React.RefObject<HTMLTextAreaElement | null>;
  keysRef: React.RefObject<((key: string) => boolean) | null>;
}) {
  const { ws, cfg } = useWs();
  const proc = item.procedure_name ?? cfg.procedures.find((p) => p.id === item.procedure_id)?.name ?? item.procedure_id;
  const detail = wsHref(ws, "patterns", item.pattern_id);
  if (decision) {
    const m = VERDICT[decision.verdict];
    return (
      <article data-card={item.pattern_id} tabIndex={-1} onFocus={onFocus} className={cn("bg-card rounded-lg border px-4 py-2 outline-none", focused && "border-foreground")}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {decision.phase === "sent" ? <Check className="text-ok size-4 shrink-0" /> : decision.phase === "error" ? <X className="text-err size-4 shrink-0" /> : <Loader2 className="text-muted-foreground size-4 shrink-0 animate-spin" />}
          <span className="min-w-0 flex-1 truncate font-medium">{item.name}</span>
          <span className={cn("rounded-full px-2 py-px text-xs font-medium", m.tone)}>{m.label}</span>
          {decision.phase === "waiting" || decision.phase === "sending" ? (
            <Button size="xs" variant="outline" onClick={onUndo} disabled={decision.phase === "sending"}>
              <Undo2 /> Undo <kbd className="text-muted-foreground font-mono text-sm">u</kbd>
            </Button>
          ) : (
            <Button size="xs" variant="ghost" onClick={onReopen}>
              Change
            </Button>
          )}
        </div>
        {decision.note && <p className="text-muted-foreground mt-1 pl-7 text-sm">“{decision.note}”</p>}
        {decision.phase === "error" && (
          <div className="mt-2 pl-7">
            <ErrorBanner error={decision.error} />
          </div>
        )}
        {decision.phase === "sent" && suggests(decision.verdict) && (
          <div className="mt-2 pl-7">
            {decision.suggestion ? <SuggestionState patternId={item.pattern_id} initial={decision.suggestion} /> : <AskThenWrite patternId={item.pattern_id} initial={decision.note} />}
          </div>
        )}
        {decision.phase === "sent" && !suggests(decision.verdict) && <p className="text-muted-foreground mt-1 pl-7 text-sm">Hidden from Insights rates from now on.</p>}
      </article>
    );
  }
  if (!focused)
    return (
      <button
        type="button"
        data-card={item.pattern_id}
        onClick={onFocus}
        onFocus={onFocus}
        className="bg-card hover:border-input grid w-full grid-cols-[0.5rem_minmax(0,1fr)_auto] items-center gap-3 rounded-lg border px-4 py-2.5 text-left sm:grid-cols-[0.5rem_minmax(0,1fr)_auto_auto]"
      >
        <span aria-hidden className={cn("size-2 rounded-full", item.review_state === "regressed" ? "bg-err" : "bg-warn")} />
        <span className="min-w-0 truncate font-medium">{item.name}</span>
        <span className="text-k-procedure hidden text-xs whitespace-nowrap sm:inline">{proc}</span>
        <span className="text-muted-foreground text-xs whitespace-nowrap tabular-nums">{plural(item.sessions, "session", "sessions")}</span>
      </button>
    );
  return (
    <article data-card={item.pattern_id} tabIndex={-1} onFocus={onFocus} aria-label={item.name} className="bg-card border-foreground grid scroll-mt-20 gap-4 rounded-lg border px-4 py-4 outline-none">
      {item.review_state === "regressed" && (
        <div className="bg-err-soft text-err flex items-start gap-2 rounded-md px-2 py-2 text-sm font-medium" role="note">
          <RotateCcw className="mt-0.5 size-3.5 shrink-0" />
          <span>
            {item.review_note ?? (item.fix_release ? `Came back after release ${item.fix_release}.` : "Came back after the fix.")} Look again: the fix didn't hold.
          </span>
        </div>
      )}
      <RewordedNote reason={item.review_reason} />
      <header className="grid gap-1">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h3 className="min-w-0 flex-1 text-lg font-semibold">
            <HrefLink href={detail} className="text-foreground">
              {item.name}
            </HrefLink>
          </h3>
          <span className="text-muted-foreground flex items-center gap-2 text-xs">
            {item.verdict && !!item.new_findings && <span>{plural(item.new_findings, "new finding", "new findings")} since</span>}
            {item.verdict && <VerdictChip v={item.verdict} />}
            <ReviewStateChip state={item.review_state} note={item.review_note} />
          </span>
        </div>
        <p className="text-muted-foreground text-xs tabular-nums">
          In {plural(item.sessions, "session", "sessions")} · last seen {ago(item.last_seen)}
          {item.review_state === "needs_review" && item.review_note && item.verdict && item.review_reason !== "reworded" && <> · {item.review_note}</>} ·{" "}
          <HrefLink href={detail}>Read more</HrefLink>
        </p>
      </header>
      <PatternFacts proc={proc} theme={item.theme_name} rule={item.rule} examples={item.examples.map(exOf)} procedureId={item.procedure_id} ruleId={item.rule_id} orderHref={detail} />
      <div className="border-t pt-3">
        <VerdictPicker current={item.verdict?.verdict} onPick={onPick} noteRef={noteRef} keysRef={keysRef} ruleKind={item.rule?.kind} />
      </div>
    </article>
  );
}

// --- the queue -----------------------------------------------------------------------------------------------------

const UNDO_MS = 6000;
export const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.getAttribute("role") === "combobox");

function ReviewQueue({ items, loading, error, onRetry }: { items: ReviewItem[]; loading: boolean; error: unknown; onRetry: () => void }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [decided, setDecided] = useState<Record<string, Decision>>({});
  const [focus, setFocus] = useState(0);
  const timers = useRef<Record<string, number>>({});
  const pending = useRef<Record<string, Decision>>({});
  const noteRefs = useRef<Record<string, React.RefObject<HTMLTextAreaElement | null>>>({});
  const noteRef = (id: string) => (noteRefs.current[id] ??= { current: null });
  const keyRefs = useRef<Record<string, React.RefObject<((key: string) => boolean) | null>>>({});
  const keysRef = (id: string) => (keyRefs.current[id] ??= { current: null });
  const last = useRef<string | null>(null); // the latest verdict given, for `u`: focus has moved on to the next pattern by then

  const send = useCallback(
    async (id: string, d: Decision, keepalive = false) => {
      delete pending.current[id];
      window.clearTimeout(timers.current[id]);
      setDecided((s) => (s[id] ? { ...s, [id]: { ...s[id], phase: "sending" } } : s));
      try {
        // the fix isn't written yet: the card asks what it should do first
        const r = await review.verdict(ws, id, { verdict: d.verdict, note: d.note || null, reviewer: author(), suggest: false }, keepalive);
        setDecided((s) => ({ ...s, [id]: { ...d, phase: "sent", suggestion: r?.suggestion ?? null } }));
        void qc.invalidateQueries({ queryKey: ["ws", ws, "review"] });
        void qc.invalidateQueries({ queryKey: ["ws", ws, "pattern", id] });
        void qc.invalidateQueries({ queryKey: ["ws", ws, "suggestions"] });
      } catch (e) {
        setDecided((s) => ({ ...s, [id]: { ...d, phase: "error", error: e } }));
      }
    },
    [ws, qc],
  );

  // Verdicts wait a few seconds so Undo is free; anything still waiting goes out when the page is left.
  useEffect(() => {
    const flush = () => Object.entries(pending.current).forEach(([id, d]) => void send(id, d, true));
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, [send]);

  const visible = items;
  const current = visible[Math.min(focus, visible.length - 1)];

  const pick = useCallback(
    (id: string, v: Verdict, note: string) => {
      const d: Decision = { verdict: v, note, phase: "waiting" };
      last.current = id;
      verdictLegend.close();
      pending.current[id] = d;
      setDecided((s) => ({ ...s, [id]: d }));
      window.clearTimeout(timers.current[id]);
      timers.current[id] = window.setTimeout(() => void send(id, d), UNDO_MS);
      // move on to the next card still needing a verdict
      const at = visible.findIndex((x) => x.pattern_id === id);
      const next = visible.findIndex((x, i) => i > at && !decided[x.pattern_id] && x.pattern_id !== id);
      if (next >= 0) setFocus(next);
    },
    [send, visible, decided],
  );
  const undo = (id: string) => {
    window.clearTimeout(timers.current[id]);
    delete pending.current[id];
    setDecided(({ [id]: _, ...rest }) => rest);
    setFocus(Math.max(0, visible.findIndex((x) => x.pattern_id === id)));
  };
  const reopen = (id: string) => setDecided(({ [id]: _, ...rest }) => rest);

  // The first card gets focus on load without scrolling, so the page opens at its title; later moves scroll to the card.
  const landed = useRef(false);
  useEffect(() => {
    const el = current && document.querySelector<HTMLElement>(`[data-card="${CSS.escape(current.pattern_id)}"]`);
    if (el && !el.contains(document.activeElement)) {
      el.focus({ preventScroll: true });
      if (landed.current) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    if (el) landed.current = true;
  }, [current]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || !current) return;
      const id = current.pattern_id;
      if (e.key === "j" || e.key === "ArrowDown") setFocus((f) => Math.min(f + 1, visible.length - 1));
      else if (e.key === "k" || e.key === "ArrowUp") setFocus((f) => Math.max(f - 1, 0));
      else if (!decided[id] && keysRef(id).current?.(e.key)) {
        // the card's two questions took it: an answer, or Escape back to the first
      }
      else if (e.key === "n" && !decided[id]) noteRef(id).current?.focus();
      else if (e.key === "u" && last.current && decided[last.current]?.phase === "waiting") undo(last.current);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (loading)
    return (
      <div className="grid gap-3" aria-busy>
        {[0, 1, 2].map((i) => (
          <div key={i} className="bg-card grid gap-2 rounded-lg border p-4">
            <Skel className="h-4 w-1/3" />
            <Skel className="h-5 w-2/3" />
            <Skel className="h-12" />
            <Skel className="h-8 w-1/2" />
          </div>
        ))}
      </div>
    );
  if (error)
    return (
      <Banner tone="err">
        Couldn't load the review queue. {error instanceof Error ? error.message : ""}{" "}
        <Button size="xs" variant="outline" onClick={onRetry}>
          Try again
        </Button>
      </Banner>
    );
  if (!visible.length)
    return (
      <div role="status" className="bg-card text-muted-foreground flex flex-col items-center gap-2 rounded-lg border px-4 py-10 text-center text-sm">
        <Check className="text-ok size-6" />
        <div className="text-foreground font-semibold">Nothing to review</div>
        <p>Every pattern here has a verdict. New ones show up as sessions are checked and grouped.</p>
      </div>
    );
  return (
    <div className="grid gap-3">
      {visible.map((it, i) => (
        <ReviewCard
          key={it.pattern_id}
          item={it}
          focused={current?.pattern_id === it.pattern_id}
          decision={decided[it.pattern_id]}
          onFocus={() => setFocus(i)}
          onPick={(v, note) => pick(it.pattern_id, v, note)}
          onUndo={() => undo(it.pattern_id)}
          onReopen={() => reopen(it.pattern_id)}
          noteRef={noteRef(it.pattern_id)}
          keysRef={keysRef(it.pattern_id)}
        />
      ))}
    </div>
  );
}

// --- the Patterns page ---------------------------------------------------------------------------------------------

export type PatternsSearch = { tab?: Exclude<PatternsTab, "review">; procedure?: string; theme?: string; verdict?: "none" | "has" | "any" | Verdict };

const matchesVerdict = (v: VerdictOut | null, f: PatternsSearch["verdict"]) => !f || f === "any" || (f === "none" ? !v : f === "has" ? !!v : v?.verdict === f);

/** Patterns needing a look: no verdict yet, or a fix that didn't hold. */
export function useNeedsReview(ws: string) {
  const res = useQuery({ ...rq.review(ws), retry: false });
  return res.data ? (res.data.total ?? res.data.items.length) : null;
}

const TABS: { tab: PatternsTab; label: string; help: string; badge: string }[] = [
  { tab: "review", label: "Needs review", help: "Patterns nobody has judged yet, and fixes that didn't hold.", badge: "Patterns waiting for a verdict, or that came back after a fix" },
  { tab: "open", label: "Open", help: "Real problems with no fix applied yet. Write a fix, or review the one proposed.", badge: "Real problems with no fix applied yet" },
  { tab: "fixing", label: "Fixing", help: "Real mistakes and wrong SOPs with a fix applied or in a pull request, waiting to go live and for 10 sessions on it. Acceptable and Checker is wrong close instead: under All patterns, and Checker is wrong also on Fine tune.", badge: "Fixes waiting for a release and 10 sessions on it" },
  { tab: "fixed", label: "Fixed", help: "Real mistakes and wrong SOPs whose fix held: 10 sessions on the new release with no findings. Acceptable and Checker is wrong close instead, under All patterns.", badge: "Fixes that held" },
  { tab: "all", label: "All patterns", help: "Every pattern, by procedure and theme, with its verdict and state.", badge: "Every pattern" },
];

export function PatternsView({ search, onSearch }: { search: PatternsSearch; onSearch: (s: PatternsSearch) => void }) {
  const { ws, cfg } = useWs();
  const qc = useQueryClient();
  const tab: PatternsTab = search.tab ?? "review";
  const reviewRes = useQuery(rq.review(ws));
  const stateRes = useQuery({ ...rq.review(ws, tab === "all" || tab === "review" ? undefined : TAB_STATES[tab]), enabled: tab !== "all" && tab !== "review" });
  const all = useQuery(q.patterns(ws));
  const [reclustering, setReclustering] = useState(false);
  // Cards shown on this visit stay put (collapsed once decided), even after the queue drops them.
  const sticky = useRef(new Map<string, ReviewItem>());
  const verdictFilter = search.verdict;
  const withVerdictFilter = tab === "review" || tab === "all";

  const reviewItems = useMemo(() => reviewRes.data?.items ?? [], [reviewRes.data]);
  const counts = reviewRes.data?.counts ?? stateRes.data?.counts;
  const countOf = (t: PatternsTab): number | null =>
    t === "all" ? (all.data ? all.data.items.reduce((n, p) => n + 1 + p.sub_patterns.filter((s) => s.findings > 0 || s.kind === "sub").length, 0) : null) : counts ? TAB_STATES[t].reduce((n, st) => n + (counts[st] ?? 0), 0) : null;
  const procName = (id: string) => cfg.procedures.find((p) => p.id === id)?.name ?? id;

  const themes = useMemo(() => {
    const names = new Set<string>();
    for (const i of reviewItems) if (i.theme_name) names.add(i.theme_name);
    for (const p of all.data?.items ?? []) if (p.theme_name) names.add(p.theme_name);
    return [...names].sort();
  }, [reviewItems, all.data]);
  const procedures = useMemo(() => {
    const ids = new Set<string>([...reviewItems.map((i) => i.procedure_id), ...(all.data?.items ?? []).map((p) => p.procedure_id)]);
    return [...ids].map((id) => [id, procName(id)] as [string, string]).sort((a, b) => a[1].localeCompare(b[1]));
  }, [reviewItems, all.data, cfg]);

  const inScope = (i: ReviewItem) => (!search.procedure || i.procedure_id === search.procedure) && (!search.theme || i.theme_name === search.theme);
  const fresh = reviewItems.filter(inScope).filter((i) => sticky.current.has(i.pattern_id) || matchesVerdict(i.verdict, verdictFilter));
  const freshIds = new Set(fresh.map((i) => i.pattern_id));
  // Came-back patterns first, then the most sessions.
  const queue = [...fresh, ...[...sticky.current.values()].filter((i) => !freshIds.has(i.pattern_id))].sort(
    (a, b) => Number(b.review_state === "regressed") - Number(a.review_state === "regressed") || b.sessions - a.sessions,
  );
  // Remember what's shown, so a verdict doesn't make its card vanish mid-review when the queue refetches.
  useEffect(() => {
    sticky.current.clear();
  }, [tab, search.procedure, search.theme, search.verdict]);
  useEffect(() => {
    if (tab === "review") for (const i of queue) if (!sticky.current.has(i.pattern_id)) sticky.current.set(i.pattern_id, i);
  });

  const lastClustered = reviewRes.data?.clustered_at ?? all.data?.clustered_at ?? null;

  const recluster = async () => {
    setReclustering(true);
    try {
      const r = await call(sdk.reclusterPatterns({ path: { workspace: ws }, body: { procedure: search.procedure ?? null, rule: null, force: false } }));
      toast.success(r.reclustered.length || r.assigned ? `Grouped: ${plural(r.assigned, "finding placed", "findings placed")}, ${plural(r.reclustered.length, "rule regrouped", "rules regrouped")}.` : "Nothing was due for regrouping.");
      await refresh(qc, ws);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setReclustering(false);
    }
  };

  const set = (patch: Partial<PatternsSearch>) => onSearch({ ...search, ...patch });
  const meta = TABS.find((t) => t.tab === tab)!;
  return (
    <Page wide>
      <PageTitle
        aside={
          <span className="ml-auto flex flex-wrap items-center gap-2">
            <Hint tip={<TipBody head="Last clustered">{lastClustered ? `Findings were last grouped into patterns ${when(lastClustered)}. It runs on its own every day; Recluster runs it now.` : "Findings haven't been grouped yet. It runs on its own every day."}</TipBody>}>
              <Note>{lastClustered ? `Last clustered ${ago(lastClustered)}` : "Not clustered yet"}</Note>
            </Hint>
            <Button size="sm" variant="outline" onClick={recluster} disabled={reclustering} title="Group new findings, and regroup rules that are due">
              <RefreshCw className={cn(reclustering && "animate-spin")} /> {reclustering ? "Reclustering…" : "Recluster"}
            </Button>
          </span>
        }
      >
        Patterns
      </PageTitle>
      <Sub>What keeps happening in your sessions, grouped by rule. Answer two questions for each one. Real mistakes and wrong SOPs get an SOP edit, and you can follow it until it holds.</Sub>
      <Tabs value={tab} onValueChange={(t) => onSearch({ procedure: search.procedure, theme: search.theme, verdict: t === "review" || t === "all" ? search.verdict : undefined, tab: t === "review" ? undefined : (t as PatternsSearch["tab"]) })} className="mb-1 gap-0">
        <div className="-mx-1 overflow-x-auto border-b px-1">
          <TabsList variant="line" aria-label="Pattern views" className="h-10 gap-0">
            {TABS.map((t) => {
              const n = countOf(t.tab);
              return (
                <TabsTrigger key={t.tab} value={t.tab} className="flex-none px-3 text-sm">
                  {t.label}
                  {n != null && n > 0 && (
                    <Hint focusable={false} tip={t.badge}>
                      <span className={cn("rounded-full px-2 text-xs tabular-nums", t.tab === "review" ? "bg-warn-soft text-warn" : "bg-muted text-muted-foreground")}>{n}</span>
                    </Hint>
                  )}
                </TabsTrigger>
              );
            })}
          </TabsList>
        </div>
      </Tabs>
      <p className="text-muted-foreground mt-2 mb-3 text-sm">{meta.help}</p>
      {tab === "review" && (
        <details
          open={verdictLegend.open()}
          onToggle={(e) => (e.currentTarget.open ? verdictLegend.reopen() : verdictLegend.close())}
          className="bg-card group mb-4 rounded-lg border px-4 py-3"
        >
          <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-semibold [&::-webkit-details-marker]:hidden">
            <ChevronRight className="text-muted-foreground size-4 transition-transform group-open:rotate-90" />
            What the answers mean
          </summary>
          <VerdictLegend className="mt-3" />
          <p className="text-muted-foreground mt-3 text-sm">A verdict waits {UNDO_MS / 1000} seconds before it's saved, so you can undo it.</p>
        </details>
      )}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <FilterSelect label="Procedure" value={search.procedure} options={procedures} onChange={(v) => set({ procedure: v })} />
        <FilterSelect label="Theme" value={search.theme} options={themes.map((t) => [t, t] as [string, string])} onChange={(v) => set({ theme: v })} />
        {withVerdictFilter && (
          <FilterSelect<NonNullable<PatternsSearch["verdict"]>>
            label="Verdict"
            value={verdictFilter === "any" ? undefined : verdictFilter}
            options={[["none", "No verdict yet"], ["has", tab === "review" ? "Has a verdict, new findings since" : "Has a verdict"], ...VERDICTS.map((v) => [v, VERDICT[v].label] as [Verdict, string])]}
            onChange={(v) => set({ verdict: v })}
          />
        )}
        {tab === "review" && (
          <Hint tip={<TipBody head="Keyboard">j / k move between patterns · 1–3 answer, then 1–2 if it was Yes · Esc goes back to the first question · n adds a note · u undoes</TipBody>}>
            <span className="text-muted-foreground ml-auto hidden items-center gap-1 text-xs sm:inline-flex">
              <Keyboard className="size-3.5" /> j k · 1–3 · n · u
            </span>
          </Hint>
        )}
      </div>
      {tab === "review" ? (
        <ReviewQueue items={queue} loading={reviewRes.isPending} error={reviewRes.error} onRetry={() => void reviewRes.refetch()} />
      ) : tab === "all" ? (
        <AllPatterns items={all.data?.items} loading={all.isPending} error={all.error} onRetry={() => void all.refetch()} search={{ ...search, verdict: verdictFilter }} procName={procName} />
      ) : (
        <StateList tab={tab} items={(stateRes.data?.items ?? []).filter(inScope)} loading={stateRes.isPending} error={stateRes.error} onRetry={() => void stateRes.refetch()} />
      )}
    </Page>
  );
}

function AllPatterns({
  items,
  loading,
  error,
  onRetry,
  search,
  procName,
}: {
  items: RulePatternOut[] | undefined;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  search: PatternsSearch;
  procName: (id: string) => string;
}) {
  const { ws } = useWs();
  if (loading)
    return (
      <div className="bg-card grid gap-2 rounded-lg border p-4" aria-busy>
        {[0, 1, 2, 3, 4].map((i) => (
          <Skel key={i} className="h-6" />
        ))}
      </div>
    );
  if (error)
    return (
      <Banner tone="err">
        Couldn't load the patterns. {error instanceof Error ? error.message : ""}{" "}
        <Button size="xs" variant="outline" onClick={onRetry}>
          Try again
        </Button>
      </Banner>
    );
  const keep = (p: PatternOut) => matchesVerdict(p.verdict ?? null, search.verdict);
  const rows = (items ?? [])
    .filter((p) => (!search.procedure || p.procedure_id === search.procedure) && (!search.theme || p.theme_name === search.theme))
    .map((p) => ({ p, subs: p.sub_patterns.filter((s) => (s.findings > 0 || s.kind === "sub") && keep(s)) }))
    .filter(({ p, subs }) => keep(p) || subs.length);
  if (!rows.length)
    return (
      <div role="status" className="bg-card text-muted-foreground rounded-lg border px-4 py-10 text-center text-sm">
        <div className="text-foreground font-semibold">No patterns {items?.length ? "match these filters" : "yet"}</div>
        <p className="mx-auto mt-1">{items?.length ? "Try another procedure, theme or verdict." : "Patterns appear once checked sessions have findings. Each rule with findings gets one; past eight findings it's split into named patterns."}</p>
      </div>
    );
  // procedure → theme → rule pattern → its patterns
  const byProc = new Map<string, Map<string, typeof rows>>();
  for (const r of rows) {
    const themes = byProc.get(r.p.procedure_id) ?? new Map<string, typeof rows>();
    const t = r.p.theme_name ?? "";
    themes.set(t, [...(themes.get(t) ?? []), r]);
    byProc.set(r.p.procedure_id, themes);
  }
  return (
    <div className="grid gap-4">
      {[...byProc.entries()].map(([pid, themes]) => (
        <section key={pid} className="bg-card min-w-0 overflow-hidden rounded-lg border">
          <header className="flex items-baseline justify-between gap-3 border-b bg-[var(--band)] px-4 py-2">
            <h2 className="text-sm font-semibold">{procName(pid)}</h2>
            <HrefLink href={wsHref(ws, "results", "procedures", pid)} className="text-xs">
              Insights
            </HrefLink>
          </header>
          {[...themes.entries()]
            .sort(([a], [b]) => (a ? (b ? a.localeCompare(b) : -1) : 1))
            .map(([theme, list]) => (
              <div key={theme} className="border-b px-4 py-3 last:border-b-0">
                <h3 className="text-muted-foreground mb-2 text-xs font-semibold tracking-wide uppercase">{theme || "Not in a theme yet"}</h3>
                <ul className="grid gap-1">
                  {list.map(({ p, subs }) => (
                    <li key={p.pattern_id}>
                      <PatternRow p={p} depth={0} rule={p.rule} split={p.sub_patterns.some((x) => x.kind === "sub")} />
                      {p.rule?.text && <div className="text-muted-foreground mb-0.5 line-clamp-1 text-xs"><RuleText text={p.rule.text} /></div>}
                      {subs.length > 0 && (
                        <ul className="ml-1 border-l pl-3">
                          {subs.map((s) => (
                            <li key={s.pattern_id}>
                              <PatternRow p={s} depth={1} />
                            </li>
                          ))}
                        </ul>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
        </section>
      ))}
    </div>
  );
}

/** One pattern in a list: name, verdict, review state and counts. A rule pattern split into patterns shows no
 * state of its own: each of them has one. */
function PatternRow({ p, depth, split, rule }: { p: PatternOut; depth: number; split?: boolean; rule?: RuleRef | null }) {
  const { ws } = useWs();
  return (
    <div className={cn("flex flex-wrap items-center gap-x-2 gap-y-0.5 py-1", depth && "text-sm")}>
      <HrefLink href={wsHref(ws, "patterns", p.pattern_id)} className={cn("min-w-0", depth === 0 && "font-semibold", p.kind === "other" && "text-muted-foreground")}>
        {p.name}
      </HrefLink>
      {p.kind === "rule" && <RuleTag rule={rule} />}
      {p.status === "retired" && <Tag>retired</Tag>}
      <VerdictChip v={p.verdict} />
      {!split && <ReviewStateChip state={p.review_state} note={p.review_note} />}
      <span className="flex-1" />
      <Hint focusable={false} tip={<TipBody head="In the last year">Sessions with a finding in it, its findings, and when it last came up.{p.suppressed ? " Suppressed findings are hidden from Insights by a verdict." : ""}</TipBody>}>
        <span className="text-muted-foreground text-xs whitespace-nowrap tabular-nums">
          {p.sessions} sessions · {p.findings} findings{p.suppressed ? ` · ${p.suppressed} suppressed` : ""} · {ago(p.last_seen)}
        </span>
      </Hint>
      {depth > 0 && p.description && <span className="text-muted-foreground basis-full truncate text-xs">{p.description}</span>}
    </div>
  );
}

// --- one pattern ---------------------------------------------------------------------------------------------------

export function PatternView({ id }: { id: string }) {
  const { ws, cfg } = useWs();
  const qc = useQueryClient();
  const [asking, setAsking] = useState<{ note: string } | null>(null); // asking what the next fix should do
  const res = useQuery({ ...rq.pattern(ws, id), refetchInterval: (query) => (query.state.data?.suggestions.some(writing) ? 2500 : false) });
  const queue = useQuery({ ...rq.review(ws), retry: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const d = res.data;
  const { index } = usePatternIndex(ws);
  if (res.isPending)
    return (
      <Page wide>
        <Skel className="mb-2 h-4 w-40" />
        <Skel className="mb-4 h-7 w-96 max-w-full" />
        <Skel className="h-40" />
      </Page>
    );
  if (res.isError || !d)
    return (
      <Page>
        <ErrorBanner error={res.error} />
        <HrefLink href={wsHref(ws, "patterns")}>All patterns</HrefLink>
      </Page>
    );
  const proc = cfg.procedures.find((p) => p.id === d.procedure_id)?.name ?? d.procedure_id;
  const item = queue.data?.items.find((i) => i.pattern_id === id);
  const verdict = d.verdict ?? null;
  const history = d.verdict_history;
  const suggestions = d.suggestions;
  const examples: Ex[] = d.review_examples?.length // an older server, or the demo's recording, has none
    ? d.review_examples.map(exOf)
    : item
      ? item.examples.map(exOf)
      : d.examples.map((e, i) => ({ key: `${e.session_id}-${i}`, session_id: e.session_id, started_at: null, turn: e.turn, excerpt: e.quote }));
  const ordered = examples.find((e) => e.order && swapOf(e.order, d.rule.index));
  const split = d.kind === "rule" && d.sub_patterns.some((x) => x.kind === "sub");

  const pick = async (v: Verdict, note: string) => {
    setBusy(true);
    setError(null);
    try {
      await review.verdict(ws, id, { verdict: v, note: note || null, reviewer: author(), suggest: false });
      toast.success(`Marked ${verdictPhrase(v)}.`);
      setAsking(suggests(v) ? { note } : null);
      await Promise.all([qc.invalidateQueries({ queryKey: ["ws", ws, "pattern", id] }), qc.invalidateQueries({ queryKey: ["ws", ws, "review"] }), qc.invalidateQueries({ queryKey: ["ws", ws, "suggestions"] })]);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const subs = d.sub_patterns.filter((s) => s.findings > 0 || s.kind === "sub");
  const facts: [string, ReactNode][] = [
    ["Rule", <HrefLink href={wsHref(ws, "results", "procedures", d.procedure_id, "rules", d.rule_id)}>{ruleName(d.rule.kind, d.rule.index)}</HrefLink>],
    ["Kind", d.kind === "rule" ? "Every finding on the rule" : d.kind === "sub" ? `One of the ${ruleWord(d.rule.kind)}'s patterns` : "Findings that fit none of its other patterns"],
    ["All time", plural(d.total_findings, "finding", "findings")],
  ];
  if (d.suppressed_count) facts.push(["Suppressed", plural(d.suppressed_count, "finding hidden from rates", "findings hidden from rates")]);
  return (
    <Page wide>
      <div className="text-muted-foreground mb-2 text-sm">
        <HrefLink href={wsHref(ws, "patterns")}>Patterns</HrefLink> / {proc}
        {d.parent_id && (
          <>
            {" "}
            / <HrefLink href={wsHref(ws, "patterns", d.parent_id)}>{ruleName(d.rule.kind, d.rule.index)}</HrefLink>
          </>
        )}
      </div>
      <PageTitle
        aside={
          <span className="flex flex-wrap items-center gap-2">
            {d.kind === "rule" && <RuleTag rule={d.rule} className="text-sm" />}
            <VerdictChip v={verdict} className="text-sm" />
            {!split && <ReviewStateChip state={d.review_state} note={d.review_note} className="text-sm" />}
          </span>
        }
      >
        {d.name}
      </PageTitle>
      <p className="text-muted-foreground -mt-1 mb-3 text-sm tabular-nums">
        In {plural(d.sessions, "session", "sessions")} · first seen {when(d.first_seen) || "—"} · last seen {when(d.last_seen) || "—"}
      </p>
      {d.kind !== "rule" && d.description && <p className="mb-4 text-sm">{d.description}</p>}
      <RewordedNote reason={d.review_reason} className="mb-3" />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4">
          <section className="bg-card rounded-lg border px-4 py-4">
            <PatternFacts proc={proc} procHref={wsHref(ws, "results", "procedures", d.procedure_id)} theme={d.theme_name} rule={d.rule} procedureId={d.procedure_id} ruleId={d.rule_id} />
          </section>
          {ordered?.order && (
            <section className="bg-card rounded-lg border px-4 py-3">
              <header className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-sm font-semibold">Order of the steps</h2>
                <HrefLink href={sessionAt(ws, ordered, d.procedure_id, d.rule_id)} className="text-xs">
                  In the session {ordered.started_at ? `of ${when(ordered.started_at)}` : ""}
                </HrefLink>
              </header>
              <FullOrder order={ordered.order} index={d.rule.index} />
            </section>
          )}
          {!split && (
            <section className="bg-card rounded-lg border px-4 py-4">
              {verdict && <h2 className="mb-2 text-sm font-semibold">Change the verdict</h2>}
              {error != null && <ErrorBanner error={error} />}
              <VerdictPicker current={verdict?.verdict} busy={busy} onPick={(v, n) => void pick(v, n)} ruleKind={d.rule.kind} />
            </section>
          )}
          <section className="bg-card rounded-lg border">
            <header className="flex flex-wrap items-baseline justify-between gap-2 border-b bg-[var(--band)] px-4 py-2">
              <h2 className="text-sm font-semibold">Examples</h2>
              <span className="text-muted-foreground text-xs tabular-nums">
                {plural(d.session_ids.length, "session", "sessions")} · {plural(d.findings + d.suppressed, "finding", "findings")} in the last year
                {d.suppressed > 0 && ` (${d.suppressed} hidden from Insights by the verdict)`} ·{" "}
                <HrefLink href={sessionsHref(ws, { pattern: id })}>All sessions</HrefLink>
              </span>
            </header>
            <div className="px-4 py-3">
              <Examples items={examples} procedureId={d.procedure_id} ruleId={d.rule_id} />
            </div>
          </section>
          {subs.length > 0 && (
            <section className="bg-card rounded-lg border px-4 py-3">
              <h2 className="mb-2 text-sm font-semibold">Patterns on this {ruleWord(d.rule.kind)}</h2>
              <ul>
                {subs.map((s) => (
                  <li key={s.pattern_id}>
                    <PatternRow p={index.get(s.pattern_id) ?? s} depth={1} />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4">
          {!split ? (
            <FixTimeline d={d} />
          ) : (
            <section className="bg-card rounded-lg border px-4 py-3">
              <h2 className="mb-1 text-sm font-semibold">Where it stands</h2>
              <Note>This {ruleWord(d.rule.kind)} has several patterns, listed under it. Each one has its own verdict and fix.</Note>
            </section>
          )}
          <section className="bg-card rounded-lg border px-4 py-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">Fixes</h2>
              {verdict && suggests(verdict.verdict) && (
                <Button size="xs" variant="outline" disabled={busy || !!asking} onClick={() => setAsking({ note: "" })}>
                  <Sparkles /> {suggestions.length ? "Write again" : "Write a fix"}
                </Button>
              )}
            </div>
            {asking && verdict && suggests(verdict.verdict) && (
              <div className="mb-3 border-b pb-3">
                <AskThenWrite key={asking.note} patternId={id} initial={asking.note} onCancel={() => setAsking(null)} />
              </div>
            )}
            {suggestions.length ? (
              <ul className="grid grid-cols-[minmax(0,1fr)] gap-2">
                {suggestions.map((s) => (
                  <li key={s.suggestion_id} className="flex items-center gap-2 text-sm">
                    <SuggestionStatusTag status={s.status} />
                    <ConflictTag freshness={s.freshness} />
                    <HrefLink href={wsHref(ws, "suggestions", s.suggestion_id)} className="min-w-0 flex-1 truncate">
                      {summaryOf(s) ?? "Suggested change"}
                    </HrefLink>
                    <span className="text-muted-foreground text-xs">{ago(s.created_at)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <Note>{verdict && suggests(verdict.verdict) ? "None yet." : "Any answer but “sopops got it wrong” has sopops draft an edit to the SOP."}</Note>
            )}
          </section>
          <section className="bg-card rounded-lg border px-4 py-3">
            <h2 className="mb-2 text-sm font-semibold">Verdict history</h2>
            {history.length ? (
              <ol className="grid grid-cols-[minmax(0,1fr)] gap-2">
                {history.map((h, i) => (
                  <li key={i} className="text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <VerdictChip v={h} />
                      <span className="text-muted-foreground text-xs">
                        {h.reviewer ?? "Someone"}, {when(h.created_at)}
                      </span>
                    </div>
                    {h.note && <p className="text-muted-foreground mt-0.5">“{h.note}”</p>}
                  </li>
                ))}
              </ol>
            ) : (
              <Note>No verdict yet.</Note>
            )}
          </section>
          <section className="bg-card rounded-lg border px-4 py-3">
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
              {facts.map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
            {verdict ? (
              <a href={regressionHref(ws, id)} download className="text-primary mt-3 inline-flex items-center gap-2 text-sm hover:underline" title="Its findings labeled by the verdict, one JSON object per line, for re-checking after a change">
                <Download className="size-3.5" /> Regression cases (NDJSON)
              </a>
            ) : (
              <Note className="mt-3 block text-xs">Regression cases can be downloaded once it has a verdict.</Note>
            )}
          </section>
        </div>
      </div>
    </Page>
  );
}
