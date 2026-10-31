// Spot checks: one batch at a time of rules the checker passed, spread across procedures and agents, close calls
// first. Grouped by call, so one transcript answers all its sampled rules. Like the review queue: the rule you're on is a
// card with labelled facts and two questions (was the checker right? if not, what actually happened?), the others are
// one-line rows. j / k move between rules, 1 / 2 answer, n adds a note.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronRight, Loader2, X } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ruleName } from "@/lib/insights";
import { plural, when } from "@/lib/kinds";
import { KIND_STATUSES, RULE_STATUS, STATUS_HELP, type ReviewItem, type RuleStatus, type SpotCheckItem, type SpotCheckResponse, review, rq, statusPhrase } from "@/lib/review";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ErrorBanner, RuleText, Tag } from "./bits";
import { author } from "./form";
import { HrefLink } from "./href-link";
import { Skel } from "./insights/kit";
import { typing } from "./review";
import { FilterSelect } from "./sessions";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";

type Why = SpotCheckItem["why"][number];
const WHY: Record<Why, string> = { close_call: "Close call", review_changed: "Reviewer changed it", sample: "Random" };
const SIZES = [10, 25, 50, 100];

/** The statuses to pick from when the checker was wrong: the kind's others, in its order. */
export const othersFor = (i: SpotCheckItem): RuleStatus[] => KIND_STATUSES[i.kind].filter((s) => s !== i.status);

type Source = "batch" | "marked";
const SOURCE: Record<Source, string> = { batch: "Spot checks", marked: "Marked Checker is wrong" };
type Filters = { source?: Source; agent?: string; procedure?: string; rule?: string; why?: Why; since?: string; until?: string; sort: "value" | "newest" };
type Pending = Record<string, { status: RuleStatus; phase: "sending" | "error"; error?: unknown }>;

const pct = (x: number) => `${Math.round(x * 100)}%`;
const label = (s: RuleStatus) => statusPhrase(s).replace(/^./, (c) => c.toUpperCase());

/** Why sopops picked this rule, in plain words. */
function whyText(item: SpotCheckItem) {
  if (item.why.includes("close_call") && item.decision_p != null && item.decision_threshold != null)
    return `A close call: the checker's score was ${pct(item.decision_p)}, with its line at ${pct(item.decision_threshold)}.`;
  if (item.why.includes("review_changed") && item.decided_status) return `The reviewer model changed it: the first pass said ${statusPhrase(item.decided_status)}.`;
  return "Picked at random.";
}

function MoreButton({ initial, onDraw, busy }: { initial: number; onDraw: (n: number) => void; busy: boolean }) {
  const [n, setN] = useState(initial);
  const sizes = SIZES.includes(initial) ? SIZES : [...SIZES, initial].sort((a, b) => a - b);
  return (
    <span className="inline-flex items-center gap-2">
      <Button onClick={() => onDraw(n)} disabled={busy}>
        {busy && <Loader2 className="animate-spin" />} Give me {n} more
      </Button>
      <select aria-label="How many" className="bg-background h-8 border px-2 text-sm" value={n} onChange={(e) => setN(Number(e.target.value))}>
        {sizes.map((x) => (
          <option key={x} value={x}>
            {x}
          </option>
        ))}
      </select>
    </span>
  );
}

function Progress({ d }: { d: SpotCheckResponse }) {
  const done = d.size ? Math.round((d.answered / d.size) * 100) : 0;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="text-sm font-semibold tabular-nums" aria-live="polite">
        {d.answered} of {d.size} checked
      </span>
      <div className="bg-muted h-2 min-w-32 flex-1 overflow-hidden" role="progressbar" aria-valuemin={0} aria-valuemax={d.size} aria-valuenow={d.answered} aria-label="Batch progress">
        <div className="bg-primary h-full transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${done}%` }} />
      </div>
      <span className="text-muted-foreground text-xs tabular-nums">{plural(d.corrections, "wrong call", "wrong calls")} found</span>
    </div>
  );
}

/** The two questions: was the checker right (1 yes, 2 no), then, after no, what actually happened. Keys come through
 * `keysRef` (the page's keyboard); the line under the buttons says what the hovered answer means. */
function SpotPicker({
  item,
  busy,
  onPick,
  keysRef,
}: {
  item: SpotCheckItem;
  busy?: boolean;
  onPick: (s: RuleStatus, note: string) => void;
  keysRef: React.RefObject<((key: string) => boolean) | null>;
}) {
  const [note, setNote] = useState("");
  const [noting, setNoting] = useState(false);
  const [second, setSecond] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const others = othersFor(item);
  const pick = (s: RuleStatus) => {
    onPick(s, note.trim());
    setNote("");
    setNoting(false);
    setSecond(false);
    setHover(null);
  };
  keysRef.current = (key) => {
    const n = Number(key);
    if (key === "n") {
      setNoting(true);
      setTimeout(() => noteRef.current?.focus());
    } else if (!second && key === "1") pick(item.status);
    else if (!second && key === "2") setSecond(true);
    else if (second && Number.isInteger(n) && n >= 1 && n <= others.length) pick(others[n - 1]);
    else if (second && key === "Escape") setSecond(false);
    else return false;
    return true;
  };
  const hint =
    hover === "yes" ? (
      <>
        <span className="text-foreground font-medium">Checker was right.</span> A confirmation: training data too.
      </>
    ) : hover === "no" ? (
      <>
        <span className="text-foreground font-medium">Checker was wrong.</span> Next: what actually happened?
      </>
    ) : hover ? (
      <>
        <span className="text-foreground font-medium">{label(hover as RuleStatus)}.</span> {STATUS_HELP[hover as RuleStatus]}
      </>
    ) : second ? (
      "Pick what the agent really did. Your answer is training data for the checker."
    ) : (
      "Not sure? Open the call and read around the quote."
    );
  const answers: { key: string; label: string; h: string; act: () => void }[] = second
    ? others.map((s, i) => ({ key: String(i + 1), label: label(s), h: s, act: () => pick(s) }))
    : [
        { key: "1", label: "Yes", h: "yes", act: () => pick(item.status) },
        { key: "2", label: "No", h: "no", act: () => setSecond(true) },
      ];
  return (
    <div className="grid gap-2">
      {second && (
        <p className="text-muted-foreground text-xs">
          Was the checker right? <span className="text-foreground font-medium">No</span> ·{" "}
          <button type="button" className="text-primary underline underline-offset-2" onClick={() => setSecond(false)}>
            change
          </button>
        </p>
      )}
      <p className="text-sm font-semibold">{second ? "What actually happened?" : "Was the checker right?"}</p>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-2 sm:flex sm:flex-wrap sm:items-center">
        {answers.map((a) => (
          <Button
            key={a.h}
            size="sm"
            variant="outline"
            disabled={busy}
            className="justify-start"
            onMouseEnter={() => setHover(a.h)}
            onMouseLeave={() => setHover(null)}
            onFocus={() => setHover(a.h)}
            onBlur={() => setHover(null)}
            onClick={a.act}
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
          ref={noteRef}
          autoFocus={noting}
          className="min-h-12 text-sm"
          placeholder="Why? (optional; kept with your answer and the training data)"
          aria-label="Note for the answer"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") (e.target as HTMLElement).blur();
          }}
        />
      )}
    </div>
  );
}

/** The rule you're on: the rule as its title, the labelled facts, then the two questions. */
function SpotCard({ item, pending, onPick, keysRef }: { item: SpotCheckItem; pending?: Pending[string]; onPick: (s: RuleStatus, note: string) => void; keysRef: React.RefObject<((key: string) => boolean) | null> }) {
  const [first, ...rest] = item.evidence;
  const quote = (e: SpotCheckItem["evidence"][number], i: number) => (
    <li key={i} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border px-2 py-1.5 text-sm">
      <span className="text-muted-foreground text-xs whitespace-nowrap">{e.source === "tool_calls" ? "tool call" : `turn ${e.turn}`}</span>
      <span className="min-w-0">“{e.quote}”</span>
    </li>
  );
  return (
    <article data-item={item.spot_check_id} tabIndex={-1} aria-label={item.text} className="bg-card border-foreground grid scroll-mt-20 gap-4 border px-4 py-4 outline-none">
      <h3 className="text-lg font-semibold">
        <RuleText text={item.text} />
      </h3>
      <dl className="grid grid-cols-[minmax(0,1fr)] gap-x-5 gap-y-1 text-sm sm:grid-cols-[max-content_minmax(0,1fr)] sm:gap-y-3">
        <dt className="text-muted-foreground text-xs sm:text-sm">Procedure</dt>
        <dd className="mb-2 min-w-0 sm:mb-0">
          <span className="text-k-procedure font-medium">{item.procedure_name ?? item.procedure_id}</span>
          <span className="text-muted-foreground"> › {ruleName(item.kind, item.index)}</span>
        </dd>
        <dt className="text-muted-foreground text-xs sm:text-sm">Checker said</dt>
        <dd className="mb-2 min-w-0 sm:mb-0">
          <Tag className={RULE_STATUS[item.status].tone}>{RULE_STATUS[item.status].label}</Tag>
          {item.reason && <span className="text-muted-foreground"> {item.reason}</span>}
        </dd>
        <dt className="text-muted-foreground text-xs sm:text-sm">Evidence</dt>
        <dd className="mb-2 min-w-0 sm:mb-0">
          {first ? (
            <div className="grid gap-1.5">
              <ul className="grid grid-cols-[minmax(0,1fr)]">{quote(first, 0)}</ul>
              {rest.length > 0 && (
                <details className="group">
                  <summary className="text-primary flex cursor-pointer list-none items-center gap-1 text-xs [&::-webkit-details-marker]:hidden">
                    <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
                    {rest.length} more
                  </summary>
                  <ul className="mt-1.5 grid grid-cols-[minmax(0,1fr)] gap-1.5">{rest.map((e, i) => quote(e, i + 1))}</ul>
                </details>
              )}
            </div>
          ) : (
            <span className="text-muted-foreground">No quote</span>
          )}
        </dd>
        <dt className="text-muted-foreground text-xs sm:text-sm">Why this one</dt>
        <dd className="text-muted-foreground min-w-0">{whyText(item)}</dd>
      </dl>
      <div className="border-t pt-3">
        <SpotPicker key={item.spot_check_id} item={item} busy={pending?.phase === "sending"} onPick={onPick} keysRef={keysRef} />
        {pending?.phase === "error" && <ErrorBanner error={pending.error} />}
      </div>
    </article>
  );
}

/** Any other rule: one line. Answered ones say what you answered, with Change. */
function SpotRow({ item, pending, onFocus, onChange }: { item: SpotCheckItem; pending?: Pending[string]; onFocus: () => void; onChange: () => void }) {
  const answer = pending?.status ?? item.answer;
  if (answer) {
    const right = answer === item.status;
    return (
      <div data-item={item.spot_check_id} className="bg-card flex flex-wrap items-center gap-x-3 gap-y-1 border px-4 py-2 text-sm">
        {pending?.phase === "sending" ? <Loader2 className="text-muted-foreground size-4 shrink-0 animate-spin" /> : pending?.phase === "error" ? <X className="text-err size-4 shrink-0" /> : <Check className="text-ok size-4 shrink-0" />}
        <span className="text-muted-foreground min-w-0 flex-1 basis-[calc(100%-2rem)] truncate sm:basis-auto">
          <RuleText text={item.text} />
        </span>
        <span className={cn("px-2 py-px text-xs font-medium", right ? "bg-ok/15 text-ok" : "bg-err-soft text-err")}>{right ? "Checker was right" : `Actually ${statusPhrase(answer)}`}</span>
        {pending?.phase !== "sending" && (
          <Button size="xs" variant="ghost" onClick={onChange}>
            Change
          </Button>
        )}
        {pending?.phase === "error" && <ErrorBanner error={pending.error} />}
      </div>
    );
  }
  return (
    <button
      type="button"
      data-item={item.spot_check_id}
      onClick={onFocus}
      className="bg-card hover:border-input grid w-full grid-cols-[0.5rem_minmax(0,1fr)] items-center sm:grid-cols-[0.5rem_minmax(0,1fr)_auto] gap-3 border px-4 py-2.5 text-left"
    >
      <span aria-hidden className={cn("size-2 rounded-full", item.why.includes("sample") ? "bg-muted-foreground/40" : "bg-warn")} />
      <span className="min-w-0 truncate">
        <RuleText text={item.text} />
      </span>
      <span className="text-muted-foreground hidden text-xs whitespace-nowrap sm:inline">Checker said {statusPhrase(item.status)}</span>
    </button>
  );
}

const day = (iso: string) => iso.slice(0, 10);

export function SpotChecks() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const res = useQuery(rq.spotChecks(ws));
  const marked = useQuery(rq.review(ws, ["closed"]));
  const [filters, setFilters] = useState<Filters>({ sort: "value" });
  const [more, setMore] = useState(false);
  const [pending, setPending] = useState<Pending>({});
  const [changing, setChanging] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<string | null>(null); // null: the first rule still to answer
  const [drawing, setDrawing] = useState(false);
  const keysRef = useRef<((key: string) => boolean) | null>(null);
  const d = res.data;

  const all = useMemo(() => d?.items ?? [], [d]);
  const opts = useMemo(() => {
    const uniq = (xs: [string, string][]) => [...new Map(xs).entries()].sort((a, b) => a[1].localeCompare(b[1]));
    return {
      agents: uniq(all.filter((i) => i.agent_id).map((i) => [i.agent_id!, i.agent_id!])),
      procedures: uniq([...all.map((i): [string, string] => [i.procedure_id, i.procedure_name ?? i.procedure_id]), ...(marked.data?.items ?? []).filter((p) => p.verdict?.verdict === "checker_wrong").map((p): [string, string] => [p.procedure_id, p.procedure_name])]),
      rules: uniq(all.filter((i) => !filters.procedure || i.procedure_id === filters.procedure).map((i) => [i.rule_id, `${i.procedure_name ?? i.procedure_id}: ${ruleName(i.kind, i.index)}`])),
    };
  }, [all, marked.data, filters.procedure]);
  const shown = all.filter(
    (i) =>
      (!filters.agent || i.agent_id === filters.agent) &&
      (!filters.procedure || i.procedure_id === filters.procedure) &&
      (!filters.rule || i.rule_id === filters.rule) &&
      (!filters.why || i.why.includes(filters.why)) &&
      (!filters.since || day(i.started_at) >= filters.since) &&
      (!filters.until || day(i.started_at) <= filters.until),
  );
  // group by call: calls with rules left first, each in the order of its first item (by value or newest)
  const calls = useMemo(() => {
    const sorted = [...shown].sort((a, b) => (filters.sort === "newest" ? b.started_at.localeCompare(a.started_at) : b.weight - a.weight || b.started_at.localeCompare(a.started_at)));
    const groups = new Map<string, SpotCheckItem[]>();
    for (const i of sorted) groups.set(i.session_id, [...(groups.get(i.session_id) ?? []), i]);
    const calls = [...groups.values()];
    return [...calls.filter((c) => c.some((i) => !i.answered)), ...calls.filter((c) => c.every((i) => i.answered))];
  }, [shown, filters.sort]);
  const open = (i: SpotCheckItem) => (!i.answered || changing.has(i.spot_check_id)) && !pending[i.spot_check_id];
  const todo = calls.flat().filter(open);
  const current = todo.find((i) => i.spot_check_id === focus) ?? todo[0];

  const pick = useCallback(
    async (item: SpotCheckItem, status: RuleStatus, note: string) => {
      const k = item.spot_check_id;
      const next = todo[todo.indexOf(item) + 1] ?? todo.find((x) => x !== item);
      setPending((p) => ({ ...p, [k]: { status, phase: "sending" } }));
      setFocus(next?.spot_check_id ?? null);
      try {
        await review.label(ws, item.session_id, item.procedure_id, item.rule_id, { status, note: note || null, reviewer: author(), source: "spot_check" });
        await qc.fetchQuery({ ...rq.spotChecks(ws), staleTime: 0 });
        setPending(({ [k]: _, ...rest }) => rest);
        setChanging((c) => (c.delete(k), new Set(c)));
        void qc.invalidateQueries({ queryKey: ["ws", ws, "session", item.session_id] });
      } catch (e) {
        setPending((p) => ({ ...p, [k]: { status, phase: "error", error: e } }));
      }
    },
    [ws, qc, todo],
  );

  const draw = async (n: number) => {
    setDrawing(true);
    try {
      const fresh = await review.drawSpotChecks(ws, n);
      qc.setQueryData(rq.spotChecks(ws).queryKey, fresh);
      setFocus(null);
      setFilters({ sort: filters.sort });
    } finally {
      setDrawing(false);
    }
  };

  useEffect(() => {
    const el = current && document.querySelector<HTMLElement>(`[data-item="${CSS.escape(current.spot_check_id)}"]`);
    if (el && !el.contains(document.activeElement)) {
      el.focus({ preventScroll: true });
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [current?.spot_check_id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || !current) return;
      const at = todo.indexOf(current);
      if (e.key === "j" || e.key === "ArrowDown") setFocus(todo[Math.min(at + 1, todo.length - 1)].spot_check_id);
      else if (e.key === "k" || e.key === "ArrowUp") setFocus(todo[Math.max(at - 1, 0)].spot_check_id);
      else if (!keysRef.current?.(e.key)) return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));
  const markedShown = (marked.data?.items ?? []).filter((p) => p.verdict?.verdict === "checker_wrong" && (!filters.procedure || p.procedure_id === filters.procedure));
  const showBatch = filters.source !== "marked";
  const showMarked = filters.source !== "batch";

  let batch: ReactNode = null;
  if (res.isPending)
    batch = (
      <div className="grid gap-3" aria-busy>
        {[0, 1, 2].map((i) => (
          <div key={i} className="bg-card grid gap-2 border p-4">
            <Skel className="h-4 w-1/3" />
            <Skel className="h-5 w-2/3" />
            <Skel className="h-8 w-1/2" />
          </div>
        ))}
      </div>
    );
  else if (res.error || !d)
    batch = (
      <Banner tone="err">
        Couldn't load the spot checks. {res.error instanceof Error ? res.error.message : ""}{" "}
        <Button size="xs" variant="outline" onClick={() => void res.refetch()}>
          Try again
        </Button>
      </Banner>
    );
  else if (d.done || d.size === 0)
    batch = (
      <section role="status" className="bg-card grid justify-items-start gap-4 border px-4 py-6">
        {d.done ? (
          <>
            <h3 className="text-lg font-semibold">Batch done</h3>
            <dl className="flex flex-wrap gap-x-10 gap-y-3">
              {[
                [d.answered, "rules checked"],
                [d.corrections, "wrong calls by the checker"],
                [d.answered - d.corrections, "confirmed right"],
              ].map(([n, what]) => (
                <div key={what} className="grid">
                  <dt className="order-2 text-muted-foreground text-xs">{what}</dt>
                  <dd className="order-1 text-2xl font-semibold tabular-nums">{n}</dd>
                </div>
              ))}
            </dl>
            <p className="text-muted-foreground text-sm">Both kinds of answer are training data for your checker.</p>
          </>
        ) : (
          <>
            <h3 className="font-semibold">No spot checks waiting</h3>
            <p className="text-muted-foreground text-sm">Draw a batch of rules the checker passed, picked across procedures and agents, close calls first.</p>
          </>
        )}
        <MoreButton initial={d.default_batch} onDraw={(n) => void draw(n)} busy={drawing} />
      </section>
    );
  else
    batch = (
      <div className="grid grid-cols-[minmax(0,1fr)] gap-2">
        {!calls.length && <p className="text-muted-foreground text-sm">Nothing in this batch matches the filters.</p>}
        {calls.map((items) => {
          const first = items[0];
          const qs = new URLSearchParams({ procedure: first.procedure_id, rule: first.rule_id });
          return (
            <section key={first.session_id} className="grid grid-cols-[minmax(0,1fr)] gap-2" aria-label={`Call of ${when(first.started_at)}`}>
              <p className="text-muted-foreground mt-2 flex flex-wrap items-baseline gap-x-2 text-xs">
                <span className="text-foreground font-medium">{first.agent_id ?? "Call"}</span>
                <span>call of {when(first.started_at)}</span>
                <HrefLink href={`${wsHref(ws, "sessions", first.session_id)}?${qs}`} title="Its transcript answers all these rules">
                  Open the call
                </HrefLink>
              </p>
              {items.map((it) => {
                const item = changing.has(it.spot_check_id) ? { ...it, answered: false, answer: null } : it;
                return current?.spot_check_id === it.spot_check_id ? (
                  <SpotCard key={it.spot_check_id} item={item} pending={pending[it.spot_check_id]} onPick={(s, note) => void pick(it, s, note)} keysRef={keysRef} />
                ) : (
                  <SpotRow
                    key={it.spot_check_id}
                    item={item}
                    pending={pending[it.spot_check_id]}
                    onFocus={() => setFocus(it.spot_check_id)}
                    onChange={() => {
                      setChanging((c) => new Set(c).add(it.spot_check_id));
                      setPending(({ [it.spot_check_id]: _, ...rest }) => rest);
                      setFocus(it.spot_check_id);
                    }}
                  />
                );
              })}
            </section>
          );
        })}
      </div>
    );

  const batchFilters = showBatch && d && !d.done && d.size > 0;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
      {batchFilters && <Progress d={d} />}
      <div className="flex flex-wrap items-center gap-2">
        <FilterSelect label="Procedure" value={filters.procedure} options={opts.procedures} onChange={(v) => set({ procedure: v, rule: undefined })} />
        {batchFilters && <FilterSelect label="Agent" value={filters.agent} options={opts.agents} onChange={(v) => set({ agent: v })} />}
        {more && (
          <>
            <FilterSelect<Source> label="Source" value={filters.source} options={(Object.keys(SOURCE) as Source[]).map((x) => [x, SOURCE[x]])} onChange={(v) => set({ source: v })} />
            {batchFilters && (
              <>
                <FilterSelect label="Rule" value={filters.rule} options={opts.rules} onChange={(v) => set({ rule: v })} />
                <FilterSelect<Why> label="Reason" value={filters.why} options={(Object.keys(WHY) as Why[]).map((w) => [w, WHY[w]])} onChange={(v) => set({ why: v })} />
                <label className="text-muted-foreground flex items-center gap-1 text-xs">
                  From <input type="date" className="bg-background h-8 border px-2 text-sm" value={filters.since ?? ""} onChange={(e) => set({ since: e.target.value || undefined })} />
                </label>
                <label className="text-muted-foreground flex items-center gap-1 text-xs">
                  to <input type="date" className="bg-background h-8 border px-2 text-sm" value={filters.until ?? ""} onChange={(e) => set({ until: e.target.value || undefined })} />
                </label>
                <select aria-label="Sort" className="bg-background h-8 border px-2 text-sm" value={filters.sort} onChange={(e) => set({ sort: e.target.value as Filters["sort"] })}>
                  <option value="value">Most valuable first</option>
                  <option value="newest">Newest first</option>
                </select>
              </>
            )}
          </>
        )}
        <Button size="xs" variant="ghost" className="text-primary" onClick={() => setMore((m) => !m)} aria-expanded={more}>
          {more ? "Fewer filters" : "More filters"}
        </Button>
      </div>
      {showBatch && batch}
      {showMarked && <MarkedRows items={markedShown} loading={marked.isPending} error={marked.error} onRetry={() => void marked.refetch()} alone={!showBatch} />}
    </div>
  );
}

/** Patterns marked "Checker is wrong" on Patterns: already answered, listed here with the spot checks as the checker's training data. */
function MarkedRows({ items, loading, error, onRetry, alone }: { items: ReviewItem[]; loading: boolean; error: unknown; onRetry: () => void; alone: boolean }) {
  const { ws } = useWs();
  if (loading) return null;
  if (error)
    return (
      <Banner tone="err">
        Couldn't load the patterns marked Checker is wrong. {error instanceof Error ? error.message : ""}{" "}
        <Button size="xs" variant="outline" onClick={onRetry}>
          Try again
        </Button>
      </Banner>
    );
  if (!items.length) return alone ? <p className="text-muted-foreground text-sm">Nothing marked Checker is wrong on Patterns yet.</p> : null;
  return (
    <section className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-2">
      <h3 className="text-sm font-semibold">Marked Checker is wrong on Patterns</h3>
      <ul className="bg-card divide-y border">
        {items.map((p) => (
          <li key={p.pattern_id} className="grid gap-0.5 px-4 py-2.5 text-sm">
            <div className="flex flex-wrap items-baseline gap-x-3">
              <HrefLink href={wsHref(ws, "patterns", p.pattern_id)} className="text-foreground min-w-0 flex-1 font-medium">
                {p.name}
              </HrefLink>
              <span className="text-muted-foreground text-xs">
                <span className="text-k-procedure">{p.procedure_name}</span> · {p.verdict?.reviewer ? `${p.verdict.reviewer}, ` : ""}
                {when(p.verdict!.created_at)}
              </span>
            </div>
            {p.verdict?.note && <p className="text-muted-foreground">“{p.verdict.note}”</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}
