// Patterns: every cluster (themes, rule patterns, their failure modes), the review queue of those still needing a
// verdict, one pattern's page, and the "wrong?" control on a single finding.
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, Keyboard, Loader2, RefreshCw, Sparkles, Undo2, X } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { type PatternOut, type RulePatternOut, call, q, refresh, sdk } from "@/lib/api";
import { ruleName, sessionsHref } from "@/lib/insights";
import { plural, when } from "@/lib/kinds";
import {

  type Ex,
  type ReviewItem,
  type SuggestionSummary,
  VERDICT,
  VERDICTS,
  type Verdict,
  type VerdictOut,
  regressionHref,
  review,
  rq,
  suggests,
  summaryOf,
  exOf,
  writing,
} from "@/lib/review";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ErrorBanner, Note, PageTitle, Sub, Tag } from "./bits";
import { author } from "./form";
import { HrefLink } from "./href-link";
import { Hint, Skel, TipBody } from "./insights/kit";
import { Page } from "./item-shell";
import { FilterSelect } from "./sessions";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";

// --- small pieces --------------------------------------------------------------------------------------------------

export function ago(t: string | null | undefined): string {
  if (!t) return "—";
  const s = (Date.now() - Date.parse(t)) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  return new Date(t).toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function VerdictChip({ v, className }: { v: VerdictOut | null | undefined; className?: string }) {
  if (!v) return <span className={cn("text-muted-foreground rounded-full border border-dashed px-2 py-px text-xs whitespace-nowrap", className)}>No verdict</span>;
  const m = VERDICT[v.verdict];
  return (
    <Hint
      focusable={false}
      tip={
        <TipBody head={m.label}>
          {m.help}
          {v.inherited ? " Given on the rule pattern; this failure mode has none of its own." : ""}
          {v.carried_from ? " Carried over from a pattern a recluster retired." : ""}
          {v.note ? ` “${v.note}”` : ""} {v.reviewer ? `${v.reviewer}, ` : ""}
          {when(v.created_at)}
        </TipBody>
      }
    >
      <span className={cn("inline-block rounded-full px-2 py-px text-xs font-medium whitespace-nowrap", m.tone, className)}>
        {m.label}
        {v.inherited && <span className="font-normal opacity-75"> · from rule</span>}
      </span>
    </Hint>
  );
}

export function VerdictLegend({ className }: { className?: string }) {
  return (
    <dl className={cn("grid gap-x-4 gap-y-1 text-[13px] sm:grid-cols-2", className)}>
      {VERDICTS.map((v) => (
        <div key={v} className="flex items-baseline gap-2">
          <dt className="shrink-0">
            <kbd className="text-muted-foreground mr-1.5 rounded border px-1 font-mono text-[11px]">{VERDICT[v].key}</kbd>
            <span className={cn("rounded-full px-1.5 py-px text-xs font-medium", VERDICT[v].tone)}>{VERDICT[v].label}</span>
          </dt>
          <dd className="text-muted-foreground">{VERDICT[v].help}</dd>
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
      <div className="grid grid-cols-2 gap-1.5 sm:flex sm:flex-wrap sm:items-center">
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
            <kbd className="text-muted-foreground rounded border px-1 font-mono text-[10px] leading-4">{VERDICT[v].key}</kbd>
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
            <kbd className="rounded border px-1 font-mono text-[10px] leading-4">n</kbd> Add a note
          </Button>
        )}
      </div>
      {!compact && <p className="text-muted-foreground mt-1.5 min-h-[1.25rem] text-xs">{hover ? VERDICT[hover].help : "Pick what this pattern is. Notes are kept with the verdict."}</p>}
      {(noting || note) && (
        <Textarea
          ref={ref}
          autoFocus={noting}
          className="mt-1.5 min-h-12 text-[13px]"
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
    <ul className="grid grid-cols-[minmax(0,1fr)] gap-1.5">
      {items.slice(0, 5).map((e) => (
        <li key={e.key} className="text-[13px]">
          <HrefLink href={sessionAt(ws, e, procedureId, ruleId)} className="text-foreground hover:bg-muted block rounded-md border px-2.5 py-1.5 hover:no-underline" title="Open the session at this finding">
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

// --- suggestion status on a decided card ----------------------------------------------------------------------------

/** After a verdict that leads to a suggestion: waits for it, then links to it. */
function SuggestionState({ patternId, initial }: { patternId: string; initial: SuggestionSummary | null | undefined }) {
  const { ws } = useWs();
  const [waiting, setWaiting] = useState(true);
  const res = useQuery({ ...rq.pattern(ws, patternId), refetchInterval: waiting ? 2500 : false });
  const latest = res.data?.suggestions?.[0] ?? initial ?? null;
  const done = !!latest && !writing(latest);
  useEffect(() => {
    if (done) setWaiting(false);
  }, [done]);
  useEffect(() => {
    const t = window.setTimeout(() => setWaiting(false), 120_000);
    return () => window.clearTimeout(t);
  }, []);
  if (latest?.status === "failed")
    return (
      <span className="text-err inline-flex items-center gap-1.5 text-[13px]">
        The suggestion couldn't be written. <HrefLink href={wsHref(ws, "suggestions", latest.suggestion_id)}>Details</HrefLink>
      </span>
    );
  if (done && latest)
    return (
      <HrefLink href={wsHref(ws, "suggestions", latest.suggestion_id)} className="inline-flex items-center gap-1.5 text-[13px] font-semibold">
        <Sparkles className="size-3.5" /> Suggestion ready: review it
      </HrefLink>
    );
  if (!waiting) return <Note>The suggestion is taking a while. It will show on the Suggestions page when it's written.</Note>;
  return (
    <span className="text-muted-foreground inline-flex items-center gap-1.5 text-[13px]" role="status">
      <Loader2 className="size-3.5 animate-spin" /> Suggestion being written…
    </span>
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
    <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
      <h3 className="min-w-0 text-[15px] leading-snug font-semibold">
        <HrefLink href={detail} className="text-foreground">
          {item.name}
        </HrefLink>
      </h3>
      <span className="text-muted-foreground text-xs tabular-nums">
        {plural(item.sessions, "session", "sessions")} · {plural(item.findings, "finding", "findings")}
      </span>
    </div>
  );
  if (decision) {
    const m = VERDICT[decision.verdict];
    return (
      <article data-card={item.pattern_id} tabIndex={-1} onFocus={onFocus} className={cn("bg-card rounded-lg border px-4 py-2.5 outline-none", focused && "ring-ring/50 ring-2")}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          {decision.phase === "sent" ? <Check className="text-ok size-4 shrink-0" /> : decision.phase === "error" ? <X className="text-err size-4 shrink-0" /> : <Loader2 className="text-muted-foreground size-4 shrink-0 animate-spin" />}
          <span className="min-w-0 flex-1 truncate font-medium">{item.name}</span>
          <span className={cn("rounded-full px-2 py-px text-xs font-medium", m.tone)}>{m.label}</span>
          {decision.phase === "waiting" || decision.phase === "sending" ? (
            <Button size="xs" variant="outline" onClick={onUndo} disabled={decision.phase === "sending"}>
              <Undo2 /> Undo <kbd className="text-muted-foreground font-mono text-[10px]">u</kbd>
            </Button>
          ) : (
            <Button size="xs" variant="ghost" onClick={onReopen}>
              Change
            </Button>
          )}
        </div>
        {decision.note && <p className="text-muted-foreground mt-1 pl-7 text-[13px]">“{decision.note}”</p>}
        {decision.phase === "error" && (
          <div className="mt-1.5 pl-7">
            <ErrorBanner error={decision.error} />
          </div>
        )}
        {decision.phase === "sent" && suggests(decision.verdict) && (
          <div className="mt-1 pl-7">
            <SuggestionState patternId={item.pattern_id} initial={decision.suggestion} />
          </div>
        )}
        {decision.phase === "sent" && !suggests(decision.verdict) && <p className="text-muted-foreground mt-1 pl-7 text-[13px]">Hidden from Insights rates from now on.</p>}
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
      className={cn("bg-card scroll-mt-20 rounded-lg border outline-none", focused ? "ring-ring/50 border-primary/40 ring-2" : "hover:border-input")}
    >
      <div className="border-b px-4 py-3">
        <div className="text-muted-foreground mb-1 flex flex-wrap items-center gap-x-1.5 text-xs">
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
          {item.verdict && (
            <span className="ml-auto flex items-center gap-1.5">
              {!!item.new_findings && <span>{plural(item.new_findings, "new finding", "new findings")} since</span>}
              <VerdictChip v={item.verdict} />
            </span>
          )}
        </div>
        {head}
        {item.description && <p className="text-muted-foreground mt-1 text-[13px]">{item.description}</p>}
        {item.rule?.text && (
          <blockquote className="border-k-procedure/50 mt-2 border-l-2 pl-2.5 text-[13px]">
            <span className="text-muted-foreground">{ruleName(item.rule.kind, item.rule.index)}:</span> {item.rule.text}
          </blockquote>
        )}
        <p className="text-muted-foreground mt-2 text-xs">
          First seen {ago(item.first_seen)} · last seen {ago(item.last_seen)}
        </p>
      </div>
      <div className="px-4 py-3">
        <h4 className="text-muted-foreground mb-1.5 text-xs font-semibold">Examples</h4>
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
const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.getAttribute("role") === "combobox");

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

  useEffect(() => {
    const el = current && document.querySelector<HTMLElement>(`[data-card="${CSS.escape(current.pattern_id)}"]`);
    if (el && !el.contains(document.activeElement)) {
      el.focus({ preventScroll: true });
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
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
      <div role="status" className="bg-card text-muted-foreground flex flex-col items-center gap-1.5 rounded-lg border px-4 py-10 text-center text-[13px]">
        <Check className="text-ok size-6" />
        <div className="text-foreground font-semibold">Nothing to review</div>
        <p className="max-w-[56ch]">Every pattern matching these filters has a verdict. New ones show up here as sessions are checked and clustered.</p>
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

export type PatternsSearch = { tab?: "all"; procedure?: string; theme?: string; verdict?: "none" | "has" | "any" | Verdict };

/** Each pattern's current verdict: from the review queue when it's there, else from the pattern itself (the list
 * doesn't carry verdicts, so those are fetched one by one and cached). */
export function usePatternVerdicts(ws: string, patterns: PatternOut[]): Map<string, VerdictOut | null> {
  const queue = useQuery({ ...rq.review(ws), retry: false });
  const queued = new Map((queue.data?.items ?? []).map((i) => [i.pattern_id, i.verdict]));
  const rest = queue.isPending ? [] : patterns.filter((p) => !queued.has(p.pattern_id));
  const details = useQueries({ queries: rest.map((p) => ({ ...rq.pattern(ws, p.pattern_id), staleTime: 30_000 })) });
  const out = new Map<string, VerdictOut | null>(queued);
  rest.forEach((p, i) => details[i].data && out.set(p.pattern_id, details[i].data.verdict ?? null));
  return out;
}
const matchesVerdict = (v: VerdictOut | null, f: PatternsSearch["verdict"]) => !f || f === "any" || (f === "none" ? !v : f === "has" ? !!v : v?.verdict === f);

export function useNeedsReview(ws: string) {
  const res = useQuery({ ...rq.review(ws), retry: false });
  return res.data ? (res.data.total ?? res.data.items.length) : null;
}

export function PatternsView({ search, onSearch }: { search: PatternsSearch; onSearch: (s: PatternsSearch) => void }) {
  const { ws, cfg } = useWs();
  const qc = useQueryClient();
  const reviewRes = useQuery(rq.review(ws));
  const all = useQuery(q.patterns(ws));
  const [reclustering, setReclustering] = useState(false);
  // Cards shown on this visit stay put (collapsed once decided), even after the queue drops them.
  const sticky = useRef(new Map<string, ReviewItem>());
  const tab = search.tab ?? "review";
  const verdictFilter = search.verdict;

  const reviewItems = useMemo(() => reviewRes.data?.items ?? [], [reviewRes.data]);
  const needs = reviewRes.data ? (reviewRes.data.total ?? reviewItems.length) : null;
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

  const fresh = reviewItems
    .filter((i) => (!search.procedure || i.procedure_id === search.procedure) && (!search.theme || i.theme_name === search.theme))
    .filter((i) => sticky.current.has(i.pattern_id) || matchesVerdict(i.verdict, verdictFilter));
  const freshIds = new Set(fresh.map((i) => i.pattern_id));
  const queue = [...fresh, ...[...sticky.current.values()].filter((i) => !freshIds.has(i.pattern_id))].sort((a, b) => b.sessions - a.sessions);
  // Remember what's shown, so a verdict doesn't make its card vanish mid-review when the queue refetches.
  useEffect(() => {
    sticky.current.clear();
  }, [tab, search.procedure, search.theme, search.verdict]);
  useEffect(() => {
    if (tab === "review") for (const i of queue) if (!sticky.current.has(i.pattern_id)) sticky.current.set(i.pattern_id, i);
  });

  const lastClustered = useMemo(() => {
    const ts = (all.data?.items ?? []).map((p) => (p as RulePatternOut & { clustered_at?: string | null }).clustered_at).filter((t): t is string => !!t);
    return ts.length ? ts.reduce((a, b) => (a > b ? a : b)) : null;
  }, [all.data]);

  const recluster = async () => {
    setReclustering(true);
    try {
      const r = await call(sdk.reclusterPatterns({ path: { workspace: ws }, body: { procedure: search.procedure ?? null, rule: null, force: false } }));
      toast.success(r.reclustered.length || r.assigned ? `Clustered: ${plural(r.assigned, "finding assigned", "findings assigned")}, ${plural(r.reclustered.length, "rule reclustered", "rules reclustered")}.` : "Nothing was due for reclustering.");
      await refresh(qc, ws);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setReclustering(false);
    }
  };

  const set = (patch: Partial<PatternsSearch>) => onSearch({ ...search, ...patch });
  return (
    <Page wide>
      <PageTitle
        aside={
          <span className="ml-auto flex flex-wrap items-center gap-2">
            <Note>{lastClustered ? `Last clustered ${ago(lastClustered)}` : "Clustered daily"}</Note>
            <Button size="sm" variant="outline" onClick={recluster} disabled={reclustering} title="Assign new findings and recluster rules that are due">
              <RefreshCw className={cn(reclustering && "animate-spin")} /> {reclustering ? "Reclustering…" : "Recluster"}
            </Button>
          </span>
        }
      >
        Patterns
      </PageTitle>
      <Sub className="max-w-[90ch]">Findings grouped by rule and by failure mode. A verdict on a pattern decides what happens next: real mistakes and wrong SOPs get a suggested fix; acceptable and misjudged ones are hidden from the rates.</Sub>
      <section className="bg-card mb-4 rounded-lg border px-4 py-3">
        <h2 className="mb-2 text-[13.5px] font-semibold">What the verdicts mean</h2>
        <VerdictLegend className="sm:grid-cols-2 xl:grid-cols-4" />
        <p className="text-muted-foreground mt-3 text-xs">A verdict waits {UNDO_MS / 1000} seconds before it's saved, so Undo is free.</p>
      </section>
      <nav className="mb-4 flex gap-0.5 border-b" aria-label="Pattern views">
        {(
          [
            ["review", "Needs review", needs],
            ["all", "All patterns", all.data ? all.data.items.length : null],
          ] as const
        ).map(([t, label, n]) => (
          <button
            key={t}
            type="button"
            aria-current={tab === t ? "page" : undefined}
            onClick={() => onSearch({ procedure: search.procedure, theme: search.theme, tab: t === "all" ? "all" : undefined })}
            className={cn("text-muted-foreground hover:text-foreground -mb-px flex cursor-pointer items-center gap-1.5 border-b-2 border-transparent px-3 py-2", tab === t && "border-primary text-foreground font-medium")}
          >
            {label}
            {n != null && n > 0 && <span className={cn("rounded-full px-1.5 text-[11px] tabular-nums", t === "review" ? "bg-warn-soft text-warn" : "bg-muted")}>{n}</span>}
          </button>
        ))}
      </nav>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <FilterSelect label="Procedure" value={search.procedure} options={procedures} onChange={(v) => set({ procedure: v })} />
        <FilterSelect label="Theme" value={search.theme} options={themes.map((t) => [t, t] as [string, string])} onChange={(v) => set({ theme: v })} />
        <FilterSelect<NonNullable<PatternsSearch["verdict"]>>
          label="Verdict"
          value={verdictFilter === "any" ? undefined : verdictFilter}
          options={[["none", "No verdict yet"], ["has", tab === "review" ? "Has a verdict, new findings since" : "Has a verdict"], ...VERDICTS.map((v) => [v, VERDICT[v].label] as [Verdict, string])]}
          onChange={(v) => set({ verdict: v })}
        />
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
      ) : (
        <AllPatterns items={all.data?.items} loading={all.isPending} error={all.error} onRetry={() => void all.refetch()} search={{ ...search, verdict: verdictFilter }} procName={procName} />
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
  const flat = useMemo(() => (items ?? []).flatMap((p) => [p, ...p.sub_patterns.filter((s) => s.findings > 0 || s.kind === "sub")]), [items]);
  const verdicts = usePatternVerdicts(ws, flat);
  const verdictOf = (p: PatternOut) => verdicts.get(p.pattern_id);
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
  const keep = (p: PatternOut) => matchesVerdict(verdictOf(p) ?? null, search.verdict);
  const rows = (items ?? [])
    .filter((p) => (!search.procedure || p.procedure_id === search.procedure) && (!search.theme || p.theme_name === search.theme))
    .map((p) => ({ p, subs: p.sub_patterns.filter((s) => (s.findings > 0 || s.kind === "sub") && keep(s)) }))
    .filter(({ p, subs }) => keep(p) || subs.length);
  if (!rows.length)
    return (
      <div role="status" className="bg-card text-muted-foreground rounded-lg border px-4 py-10 text-center text-[13px]">
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
          <header className="flex items-baseline justify-between gap-3 border-b px-4 py-2.5">
            <h2 className="text-[13.5px] font-semibold">{procName(pid)}</h2>
            <HrefLink href={wsHref(ws, "insights", "procedures", pid)} className="text-xs">
              Insights
            </HrefLink>
          </header>
          {[...themes.entries()]
            .sort(([a], [b]) => (a ? (b ? a.localeCompare(b) : -1) : 1))
            .map(([theme, list]) => (
              <div key={theme} className="border-b px-4 py-3 last:border-b-0">
                <h3 className="text-muted-foreground mb-1.5 text-xs font-semibold tracking-wide uppercase">{theme || "Not in a theme yet"}</h3>
                <ul className="grid gap-1">
                  {list.map(({ p, subs }) => (
                    <li key={p.pattern_id}>
                      <PatternRow p={p} v={verdictOf(p)} depth={0} />
                      {p.rule?.text && <div className="text-muted-foreground mb-0.5 line-clamp-1 pl-0 text-xs sm:pl-0">{ruleName(p.rule.kind, p.rule.index)}: {p.rule.text}</div>}
                      {subs.length > 0 && (
                        <ul className="ml-1 border-l pl-3">
                          {subs.map((s) => (
                            <li key={s.pattern_id}>
                              <PatternRow p={s} v={verdictOf(s)} depth={1} />
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

function PatternRow({ p, v, depth }: { p: PatternOut; v: VerdictOut | null | undefined; depth: number }) {
  const { ws } = useWs();
  return (
    <div className={cn("flex flex-wrap items-center gap-x-2.5 gap-y-0.5 py-1", depth && "text-[13px]")}>
      <HrefLink href={wsHref(ws, "patterns", p.pattern_id)} className={cn("min-w-0", depth === 0 && "font-semibold", p.kind === "other" && "text-muted-foreground")}>
        {p.name}
      </HrefLink>
      {p.status === "retired" && <Tag>retired</Tag>}
      {v === undefined ? <Skel className="h-4 w-16 rounded-full" /> : <VerdictChip v={v} />}
      <span className="flex-1" />
      <span className="text-muted-foreground text-xs whitespace-nowrap tabular-nums">
        {p.sessions} sessions · {p.findings} findings{p.suppressed ? ` · ${p.suppressed} suppressed` : ""} · {ago(p.last_seen)}
      </span>
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
  const subVerdicts = usePatternVerdicts(ws, d?.sub_patterns ?? []);
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
      toast.success(`Marked ${VERDICT[v].label.toLowerCase()}.${suggests(v) ? " A suggested fix is being written." : ""}`);
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
      toast.success("A suggestion is being written.");
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
      <div className="text-muted-foreground mb-2 text-[13px]">
        <HrefLink href={wsHref(ws, "patterns")}>Patterns</HrefLink> / {proc}
        {d.parent_id && (
          <>
            {" "}
            / <HrefLink href={wsHref(ws, "patterns", d.parent_id)}>{ruleName(d.rule.kind, d.rule.index)}</HrefLink>
          </>
        )}
      </div>
      <PageTitle aside={<VerdictChip v={verdict} className="text-[13px]" />}>{d.name}</PageTitle>
      {d.description && <Sub>{d.description}</Sub>}
      {d.rule.text && (
        <blockquote className="border-k-procedure/50 mb-4 max-w-[90ch] border-l-2 pl-3 text-[14px]">
          <span className="text-muted-foreground">{ruleName(d.rule.kind, d.rule.index)}:</span> {d.rule.text}
        </blockquote>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4">
          <section className="bg-card rounded-lg border px-4 py-3.5">
            <h2 className="mb-2 text-[13.5px] font-semibold">{verdict ? "Change the verdict" : "Give a verdict"}</h2>
            {error != null && <ErrorBanner error={error} />}
            <VerdictPicker current={verdict?.verdict} busy={busy} onPick={(v, n) => void pick(v, n)} />
          </section>
          <section className="bg-card rounded-lg border">
            <header className="flex flex-wrap items-baseline justify-between gap-2 border-b px-4 py-2.5">
              <h2 className="text-[13.5px] font-semibold">Examples</h2>
              <span className="text-muted-foreground text-xs tabular-nums">
                {plural(d.sessions, "session", "sessions")} · {plural(d.findings, "finding", "findings")} in the last year ·{" "}
                <HrefLink href={sessionsHref(ws, { pattern: id })}>All sessions</HrefLink>
              </span>
            </header>
            <div className="px-4 py-3">
              <Examples items={examples} procedureId={d.procedure_id} ruleId={d.rule_id} />
            </div>
          </section>
          {subs.length > 0 && (
            <section className="bg-card rounded-lg border px-4 py-3">
              <h2 className="mb-1.5 text-[13.5px] font-semibold">Failure modes</h2>
              <ul>
                {subs.map((s) => (
                  <li key={s.pattern_id}>
                    <PatternRow p={s} v={subVerdicts.get(s.pattern_id)} depth={1} />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4">
          <section className="bg-card rounded-lg border px-4 py-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h2 className="text-[13.5px] font-semibold">Suggestions</h2>
              {verdict && suggests(verdict.verdict) && (
                <Button size="xs" variant="outline" disabled={busy} onClick={() => void regenerate()}>
                  <Sparkles /> {suggestions.length ? "Regenerate" : "Suggest a fix"}
                </Button>
              )}
            </div>
            {suggestions.length ? (
              <ul className="grid grid-cols-[minmax(0,1fr)] gap-1.5">
                {suggestions.map((s) => (
                  <li key={s.suggestion_id} className="flex items-center gap-2 text-[13px]">
                    <SuggestionStatusTag status={s.status} />
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
            <h2 className="mb-2 text-[13.5px] font-semibold">Verdict history</h2>
            {history.length ? (
              <ol className="grid grid-cols-[minmax(0,1fr)] gap-2">
                {history.map((h, i) => (
                  <li key={i} className="text-[13px]">
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
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
              {facts.map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
            {verdict ? (
              <a href={regressionHref(ws, id)} download className="text-primary mt-3 inline-flex items-center gap-1.5 text-[13px] hover:underline" title="Its findings labeled by the verdict, one JSON object per line, for re-checking after a change">
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

// --- suggestion status ---------------------------------------------------------------------------------------------

export const SUGGESTION_STATUS: Record<SuggestionSummary["status"], { label: string; tone: string }> = {
  generating: { label: "Being written", tone: "bg-muted text-muted-foreground" },
  proposed: { label: "Proposed", tone: "bg-warn-soft text-warn" },
  pr_open: { label: "PR open", tone: "bg-brand-soft text-primary" },
  applied: { label: "Applied", tone: "bg-brand-soft text-primary" },
  dismissed: { label: "Dismissed", tone: "bg-muted text-muted-foreground" },
  failed: { label: "Failed", tone: "bg-err-soft text-err" },
};

export function SuggestionStatusTag({ status, big }: { status: SuggestionSummary["status"]; big?: boolean }) {
  const s = SUGGESTION_STATUS[status] ?? { label: status, tone: "" };
  return <Tag big={big} className={cn("border-transparent", s.tone)}>{s.label}</Tag>;
}
