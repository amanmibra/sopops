// Patterns: every cluster (themes, rule patterns, their failure modes) in tabs by review state (needs review, open,
// fixing, fixed, all), one pattern's page with where its fix stands, and the "wrong?" control on a single finding.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Download, Keyboard, Loader2, RefreshCw, RotateCcw, Sparkles, Undo2, X, Check } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { type PatternOut, type RulePatternOut, type RuleRef, call, q, refresh, sdk } from "@/lib/api";
import { ruleName, sessionsHref } from "@/lib/insights";
import { plural, when } from "@/lib/kinds";
import {
  type Ex,
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
import { ConflictTag, ReviewStateChip, RewordedNote, RuleTag, SuggestionState, SuggestionStatusTag, VerdictChip, ago, usePatternIndex } from "./review-bits";
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

export function VerdictLegend({ className }: { className?: string }) {
  return (
    <dl className={cn("grid gap-x-4 gap-y-2 text-sm xl:grid-cols-[repeat(2,minmax(0,1fr))]", className)}>
      {VERDICTS.map((v) => (
        <div key={v} className="flex min-w-0 flex-wrap items-baseline gap-x-2">
          <dt className="shrink-0">
            <kbd className="text-muted-foreground mr-2 rounded border px-1 font-mono text-sm">{VERDICT[v].key}</kbd>
            <span className={cn("rounded-full px-2 py-px text-xs font-medium", VERDICT[v].tone)}>{VERDICT[v].label}</span>
          </dt>
          <dd className="text-muted-foreground min-w-0">{VERDICT[v].help}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The four verdict buttons, with an optional note. */
function VerdictPicker({
  current,
  busy,
  onPick,
  noteRef,
  compact,
}: {
  current?: Verdict | null;
  busy?: boolean;
  onPick: (v: Verdict, note: string) => void;
  noteRef?: React.RefObject<HTMLTextAreaElement | null>;
  compact?: boolean;
}) {
  const [note, setNote] = useState("");
  const [noting, setNoting] = useState(false);
  const [hover, setHover] = useState<Verdict | null>(null);
  const local = useRef<HTMLTextAreaElement>(null);
  const ref = noteRef ?? local;
  return (
    <div>
      <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
        {VERDICTS.map((v) => (
          <Button
            key={v}
            size="sm"
            variant="outline"
            disabled={busy}
            aria-pressed={current === v}
            title={VERDICT[v].help}
            className={cn("justify-start", current === v && "bg-brand-soft border-primary/40")}
            onMouseEnter={() => setHover(v)}
            onMouseLeave={() => setHover(null)}
            onFocus={() => setHover(v)}
            onBlur={() => setHover(null)}
            onClick={() => {
              onPick(v, note.trim());
              setNote("");
              setNoting(false);
            }}
          >
            <kbd className="text-muted-foreground rounded border px-1 font-mono text-sm leading-4">{VERDICT[v].key}</kbd>
            {VERDICT[v].label}
          </Button>
        ))}
        {!noting && (
          <Button
            size="sm"
            variant="ghost"
            className="text-muted-foreground col-span-2 justify-start sm:col-span-1"
            onClick={() => setNoting(true)}
          >
            <kbd className="rounded border px-1 font-mono text-sm leading-4">n</kbd> Add a note
          </Button>
        )}
      </div>
      {!compact && <p className="text-muted-foreground mt-2 min-h-[1.25rem] text-xs">{hover ? VERDICT[hover].help : "Pick what this pattern is. Notes are kept with the verdict."}</p>}
      {(noting || note) && (
        <Textarea
          ref={ref}
          autoFocus={noting}
          className="mt-2 min-h-12 text-sm"
          placeholder="Why? (optional; kept with the verdict, and given to the suggestion writer)"
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

function Examples({ items, procedureId, ruleId }: { items: Ex[]; procedureId: string; ruleId: string }) {
  const { ws } = useWs();
  if (!items.length) return <Note>No examples in the window.</Note>;
  return (
    <ul className="grid grid-cols-[minmax(0,1fr)] gap-2">
      {items.slice(0, 5).map((e) => (
        <li key={e.key} className="text-sm">
          <HrefLink href={sessionAt(ws, e, procedureId, ruleId)} className="text-foreground hover:bg-muted block rounded-md border px-2 py-2 hover:no-underline" title="Open the session at this finding">
            <span className="text-muted-foreground mr-2 text-xs whitespace-nowrap">{e.started_at ? when(e.started_at) : "Session"}</span>
            {e.excerpt ? `“${e.excerpt}”` : <span className="text-muted-foreground italic">No quote; see the reason</span>}
            {e.suppressed && <Tag className="ml-2 align-middle">suppressed</Tag>}
            {e.reason && <span className="text-muted-foreground mt-0.5 block text-xs">{e.reason}</span>}
          </HrefLink>
        </li>
      ))}
    </ul>
  );
}

// --- the review card -----------------------------------------------------------------------------------------------

type Decision = { verdict: Verdict; note: string; phase: "waiting" | "sending" | "sent" | "error"; suggestion?: SuggestionSummary | null; error?: unknown };

function ReviewCard({
  item,
  focused,
  decision,
  onFocus,
  onPick,
  onUndo,
  onReopen,
  noteRef,
}: {
  item: ReviewItem;
  focused: boolean;
  decision?: Decision;
  onFocus: () => void;
  onPick: (v: Verdict, note: string) => void;
  onUndo: () => void;
  onReopen: () => void;
  noteRef: React.RefObject<HTMLTextAreaElement | null>;
}) {
  const { ws, cfg } = useWs();
  const proc = item.procedure_name ?? cfg.procedures.find((p) => p.id === item.procedure_id)?.name ?? item.procedure_id;
  const detail = wsHref(ws, "patterns", item.pattern_id);
  const head = (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
      <h3 className="min-w-0 text-lg font-semibold">
        <HrefLink href={detail} className="text-foreground">
          {item.name}
        </HrefLink>
      </h3>
      {item.kind === "rule" && <RuleTag rule={item.rule} className="self-center" />}
      <span className="text-muted-foreground text-xs tabular-nums">
        {plural(item.sessions, "session", "sessions")} · {plural(item.findings, "finding", "findings")}
      </span>
    </div>
  );
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
          <div className="mt-1 pl-7">
            <SuggestionState patternId={item.pattern_id} initial={decision.suggestion} />
          </div>
        )}
        {decision.phase === "sent" && !suggests(decision.verdict) && <p className="text-muted-foreground mt-1 pl-7 text-sm">Hidden from Insights rates from now on.</p>}
      </article>
    );
  }
  return (
    <article
      data-card={item.pattern_id}
      tabIndex={-1}
      onFocus={onFocus}
      onClick={onFocus}
      aria-label={item.name}
      className={cn("bg-card scroll-mt-20 rounded-lg border outline-none", focused ? "border-foreground" : "hover:border-input")}
    >
      <div className="border-b px-4 py-3">
        <div className="text-muted-foreground mb-1 flex flex-wrap items-center gap-x-2 text-xs">
          <span className="text-k-procedure font-medium">{proc}</span>
          {item.theme_name && (
            <>
              <span aria-hidden>·</span>
              <span>{item.theme_name}</span>
            </>
          )}
          {item.kind !== "rule" && (
            <>
              <span aria-hidden>·</span>
              <span>{item.kind === "other" ? "unnamed findings" : "failure mode"}</span>
            </>
          )}
          <span className="ml-auto flex items-center gap-2">
            {item.verdict && !!item.new_findings && <span>{plural(item.new_findings, "new finding", "new findings")} since</span>}
            {item.verdict && <VerdictChip v={item.verdict} />}
            <ReviewStateChip state={item.review_state} note={item.review_note} />
          </span>
        </div>
        {item.review_state === "regressed" && (
          <div className="bg-err-soft text-err mb-2 flex items-start gap-2 rounded-md px-2 py-2 text-sm font-medium" role="note">
            <RotateCcw className="mt-0.5 size-3.5 shrink-0" />
            <span>
              {item.review_note ?? (item.fix_release ? `Came back after release ${item.fix_release}.` : "Came back after the fix.")} Look again: the fix didn't hold.
            </span>
          </div>
        )}
        <RewordedNote reason={item.review_reason} className="mb-2" />
        {head}
        {item.kind !== "rule" && item.description && <p className="text-muted-foreground mt-1 text-sm">{item.description}</p>}
        {item.rule?.text && (
          <blockquote className="border-k-procedure/50 mt-2 border-l-2 pl-2 text-sm">
            <span className="text-muted-foreground">{ruleName(item.rule.kind, item.rule.index)}:</span> <RuleText text={item.rule.text} />
          </blockquote>
        )}
        <p className="text-muted-foreground mt-2 text-xs">
          First seen {ago(item.first_seen)} · last seen {ago(item.last_seen)}
          {item.review_state === "needs_review" && item.review_note && item.verdict && item.review_reason !== "reworded" && <> · {item.review_note}</>}
        </p>
      </div>
      <div className="px-4 py-3">
        <h4 className="text-muted-foreground mb-2 text-xs font-semibold">Examples</h4>
        <Examples items={item.examples.map(exOf)} procedureId={item.procedure_id} ruleId={item.rule_id} />
      </div>
      <div className="bg-muted/40 border-t px-4 py-3">
        <VerdictPicker current={item.verdict?.verdict} onPick={onPick} noteRef={noteRef} />
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

  const send = useCallback(
    async (id: string, d: Decision, keepalive = false) => {
      delete pending.current[id];
      window.clearTimeout(timers.current[id]);
      setDecided((s) => (s[id] ? { ...s, [id]: { ...s[id], phase: "sending" } } : s));
      try {
        const r = await review.verdict(ws, id, { verdict: d.verdict, note: d.note || null, reviewer: author(), suggest: suggests(d.verdict) }, keepalive);
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
      const v = VERDICTS.find((x) => VERDICT[x].key === e.key);
      if (e.key === "j" || e.key === "ArrowDown") setFocus((f) => Math.min(f + 1, visible.length - 1));
      else if (e.key === "k" || e.key === "ArrowUp") setFocus((f) => Math.max(f - 1, 0));
      else if (v && !decided[id]) pick(id, v, noteRef(id).current?.value.trim() ?? "");
      else if (e.key === "n" && !decided[id]) noteRef(id).current?.focus();
      else if (e.key === "u" && decided[id] && decided[id].phase === "waiting") undo(id);
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
        <p className="max-w-[56ch]">Every pattern here has a verdict. New ones show up as sessions are checked and grouped.</p>
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
  { tab: "fixing", label: "Fixing", help: "Real mistakes and wrong SOPs with a fix applied or in a pull request, waiting to go live and for 10 sessions on it. Acceptable and Checker is wrong close instead, under All patterns.", badge: "Fixes waiting for a release and 10 sessions on it" },
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
        Mistakes
      </PageTitle>
      <Sub className="max-w-[90ch]">Findings grouped by rule and by failure mode. Say what each one is. Real mistakes and wrong SOPs get a fix, and you can follow the fix until it holds.</Sub>
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
            What the verdicts mean
          </summary>
          <VerdictLegend className="mt-3 gap-y-2" />
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
          <Hint tip={<TipBody head="Keyboard">j / k move between cards · 1–4 give a verdict · n adds a note · u undoes · Esc leaves the note</TipBody>}>
            <span className="text-muted-foreground ml-auto hidden items-center gap-1 text-xs sm:inline-flex">
              <Keyboard className="size-3.5" /> j k · 1–4 · n · u
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
        <p className="mx-auto mt-1 max-w-[60ch]">{items?.length ? "Try another procedure, theme or verdict." : "Patterns appear once checked sessions have findings. Each rule with findings gets one; past eight findings it's split into named failure modes."}</p>
      </div>
    );
  // procedure → theme → rule pattern → failure modes
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
            <HrefLink href={wsHref(ws, "insights", "procedures", pid)} className="text-xs">
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

/** One pattern in a list: name, verdict, review state and counts. A rule pattern split into failure modes shows no
 * state of its own: each failure mode has one. */
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
  const examples: Ex[] = item ? item.examples.map(exOf) : d.examples.map((e, i) => ({ key: `${e.session_id}-${i}`, session_id: e.session_id, started_at: null, turn: e.turn, excerpt: e.quote }));

  const pick = async (v: Verdict, note: string) => {
    setBusy(true);
    setError(null);
    try {
      const r = await review.verdict(ws, id, { verdict: v, note: note || null, reviewer: author(), suggest: suggests(v) });
      toast.success(`Marked ${verdictPhrase(v)}.${suggests(v) ? " Writing a fix." : ""}`);
      if (r.suggestion) qc.setQueryData(rq.pattern(ws, id).queryKey, (old) => (old ? { ...old, suggestions: [r.suggestion!, ...old.suggestions] } : old));
      await Promise.all([qc.invalidateQueries({ queryKey: ["ws", ws, "pattern", id] }), qc.invalidateQueries({ queryKey: ["ws", ws, "review"] }), qc.invalidateQueries({ queryKey: ["ws", ws, "suggestions"] })]);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const regenerate = async () => {
    setBusy(true);
    try {
      await review.suggest(ws, id);
      toast.success("Writing the fix.");
      await qc.invalidateQueries({ queryKey: ["ws", ws, "pattern", id] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const subs = d.sub_patterns.filter((s) => s.findings > 0 || s.kind === "sub");
  const facts: [string, ReactNode][] = [
    ["Procedure", <HrefLink href={wsHref(ws, "insights", "procedures", d.procedure_id)}>{proc}</HrefLink>],
    ["Rule", <HrefLink href={wsHref(ws, "insights", "procedures", d.procedure_id, "rules", d.rule_id)}>{ruleName(d.rule.kind, d.rule.index)}</HrefLink>],
    ["Theme", d.theme_name ?? "—"],
    ["Kind", d.kind === "rule" ? "Every finding on the rule" : d.kind === "sub" ? "A named failure mode" : "Findings that fit no failure mode"],
    ["First seen", when(d.first_seen) || "—"],
    ["Last seen", when(d.last_seen) || "—"],
    ["All time", plural(d.total_findings, "finding", "findings")],
  ];
  if (d.suppressed_count) facts.push(["Suppressed", plural(d.suppressed_count, "finding hidden from rates", "findings hidden from rates")]);
  return (
    <Page wide>
      <div className="text-muted-foreground mb-2 text-sm">
        <HrefLink href={wsHref(ws, "patterns")}>Mistakes</HrefLink> / {proc}
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
            {!(d.kind === "rule" && subs.some((x) => x.kind === "sub")) && <ReviewStateChip state={d.review_state} note={d.review_note} className="text-sm" />}
          </span>
        }
      >
        {d.name}
      </PageTitle>
      {d.kind !== "rule" && d.description && <Sub>{d.description}</Sub>}
      <RewordedNote reason={d.review_reason} className="mb-3 max-w-[90ch]" />
      {d.rule.text && (
        <blockquote className="border-k-procedure/50 mb-4 max-w-[90ch] border-l-2 pl-3 text-sm">
          <span className="text-muted-foreground">{ruleName(d.rule.kind, d.rule.index)}:</span> <RuleText text={d.rule.text} />
        </blockquote>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4">
          <section className="bg-card rounded-lg border px-4 py-4">
            <h2 className="mb-2 text-sm font-semibold">{verdict ? "Change the verdict" : "Give a verdict"}</h2>
            {error != null && <ErrorBanner error={error} />}
            <VerdictPicker current={verdict?.verdict} busy={busy} onPick={(v, n) => void pick(v, n)} />
          </section>
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
              <h2 className="mb-2 text-sm font-semibold">Failure modes</h2>
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
          {!(d.kind === "rule" && subs.some((x) => x.kind === "sub")) ? (
            <FixTimeline d={d} />
          ) : (
            <section className="bg-card rounded-lg border px-4 py-3">
              <h2 className="mb-1 text-sm font-semibold">Where it stands</h2>
              <Note>This rule is split into failure modes below. Each one has its own verdict and fix.</Note>
            </section>
          )}
          <section className="bg-card rounded-lg border px-4 py-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">Fixes</h2>
              {verdict && suggests(verdict.verdict) && (
                <Button size="xs" variant="outline" disabled={busy} onClick={() => void regenerate()}>
                  <Sparkles /> {suggestions.length ? "Write again" : "Write a fix"}
                </Button>
              )}
            </div>
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
              <Note>{verdict && suggests(verdict.verdict) ? "None yet." : "A verdict of “real mistake” or “SOP is wrong” gets a suggested fix."}</Note>
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
