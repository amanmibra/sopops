// Spot checks: one batch at a time of rules the checker passed, spread across procedures and agents, close calls
// first. Grouped by call, so one transcript answers all its sampled rules. Keyboard-driven like the verdict queue:
// j / k move between rules, 1 says the checker was right, 2 onward pick what actually happened. A finished batch gets a
// short celebration and "Give me N more".
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, PartyPopper } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ruleName } from "@/lib/insights";
import { plural, when } from "@/lib/kinds";
import { KIND_STATUSES, RULE_STATUS, type RuleStatus, type SpotCheckItem, type SpotCheckResponse, review, rq, statusPhrase } from "@/lib/review";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ErrorBanner, RuleText, Tag } from "./bits";
import { author } from "./form";
import { HrefLink } from "./href-link";
import { Skel } from "./insights/kit";
import { FilterSelect } from "./sessions";
import { Button } from "./ui/button";

type Why = SpotCheckItem["why"][number];
const WHY: Record<Why, string> = { close_call: "Close call", review_changed: "Reviewer changed it", sample: "Random" };
const SIZES = [10, 25, 50, 100];

/** The checker's status first (`1`: it was right), then the others in the kind's order. */
export const answersFor = (i: SpotCheckItem): RuleStatus[] => [i.status, ...KIND_STATUSES[i.kind].filter((s) => s !== i.status)];

type Filters = { agent?: string; procedure?: string; rule?: string; why?: Why; since?: string; until?: string; sort: "value" | "newest" };
type Pending = Record<string, { status: RuleStatus; phase: "sending" | "error"; error?: unknown }>;

function whyChips(item: SpotCheckItem) {
  const out: string[] = [];
  if (item.why.includes("close_call") && item.decision_p != null && item.decision_threshold != null)
    out.push(`Close call: ${item.decision_p.toFixed(2)} against ${item.decision_threshold.toFixed(2)}`);
  if (item.why.includes("review_changed") && item.decided_status) out.push(`Reviewer model changed it from ${statusPhrase(item.decided_status)}`);
  return out.length ? out : ["Picked at random"];
}

/** A small burst of confetti on a canvas, once. Nothing with reduced motion. */
function Confetti() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const w = (canvas.width = canvas.offsetWidth * devicePixelRatio);
    const h = (canvas.height = canvas.offsetHeight * devicePixelRatio);
    const css = getComputedStyle(document.documentElement);
    const colors = ["--brand", "--warn", "--err", "--k-procedure", "--ok"].map((v) => css.getPropertyValue(v).trim() || "#4fb3a9");
    const bits = Array.from({ length: 90 }, () => ({
      x: w / 2, y: h * 0.55, vx: (Math.random() - 0.5) * 14 * devicePixelRatio, vy: (-Math.random() * 12 - 6) * devicePixelRatio,
      r: (Math.random() * 4 + 3) * devicePixelRatio, a: Math.random() * Math.PI, va: (Math.random() - 0.5) * 0.3, c: colors[Math.floor(Math.random() * colors.length)],
    }));
    let frame = 0;
    let id = 0;
    const tick = () => {
      ctx.clearRect(0, 0, w, h);
      for (const b of bits) {
        b.vy += 0.35 * devicePixelRatio;
        b.x += b.vx;
        b.y += b.vy;
        b.a += b.va;
        ctx.save();
        ctx.translate(b.x, b.y);
        ctx.rotate(b.a);
        ctx.globalAlpha = Math.max(0, 1 - frame / 110);
        ctx.fillStyle = b.c;
        ctx.fillRect(-b.r, -b.r / 2, b.r * 2, b.r);
        ctx.restore();
      }
      if (++frame < 110) id = requestAnimationFrame(tick);
      else ctx.clearRect(0, 0, w, h);
    };
    id = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(id);
  }, []);
  return <canvas ref={ref} aria-hidden className="pointer-events-none absolute inset-0 h-full w-full" />;
}

function MoreButton({ initial, onDraw, busy }: { initial: number; onDraw: (n: number) => void; busy: boolean }) {
  const [n, setN] = useState(initial);
  const sizes = SIZES.includes(initial) ? SIZES : [...SIZES, initial].sort((a, b) => a - b);
  return (
    <span className="inline-flex items-center gap-2">
      <Button onClick={() => onDraw(n)} disabled={busy}>
        {busy && <Loader2 className="animate-spin" />} Give me {n} more
      </Button>
      <select aria-label="How many" className="bg-background h-8 rounded-md border px-2 text-sm" value={n} onChange={(e) => setN(Number(e.target.value))}>
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
  const pct = d.size ? Math.round((d.answered / d.size) * 100) : 0;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="text-sm font-semibold tabular-nums" aria-live="polite">
        {d.answered} / {d.size}
      </span>
      <div className="bg-muted h-2 min-w-32 flex-1 overflow-hidden rounded-full" role="progressbar" aria-valuemin={0} aria-valuemax={d.size} aria-valuenow={d.answered} aria-label="Batch progress">
        <div className="bg-primary h-full transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-muted-foreground text-xs">
        {plural(d.corrections, "correction", "corrections")} so far{d.drawn_at ? ` · drawn ${when(d.drawn_at)}` : ""}
      </span>
    </div>
  );
}

function ItemRow({
  item,
  focused,
  pending,
  onFocus,
  onPick,
  onChange,
}: {
  item: SpotCheckItem;
  focused: boolean;
  pending?: Pending[string];
  onFocus: () => void;
  onPick: (s: RuleStatus) => void;
  onChange: () => void;
}) {
  const options = answersFor(item);
  const done = item.answered && !pending;
  return (
    <li data-item={item.spot_check_id} tabIndex={-1} onFocus={onFocus} onClick={onFocus} className={cn("rounded-md border px-3 py-2 outline-none", focused ? "border-foreground" : "border-transparent hover:border-input")}>
      <div className="flex flex-wrap items-start gap-x-2 gap-y-1">
        <p className="min-w-0 flex-1 text-sm">
          <span className="text-muted-foreground">{ruleName(item.kind, item.index)}:</span> <RuleText text={item.text} />
        </p>
        {whyChips(item).map((c) => (
          <span key={c} className={cn("rounded-full px-2 py-px text-xs", item.why.includes("sample") ? "bg-muted text-muted-foreground" : "bg-warn-soft text-warn")}>
            {c}
          </span>
        ))}
      </div>
      <p className="mt-1 text-sm">
        The checker said <Tag className={RULE_STATUS[item.status].tone}>{RULE_STATUS[item.status].label}</Tag>
        {item.reason && <span className="text-muted-foreground"> {item.reason}</span>}
      </p>
      {item.evidence.length > 0 && (
        <ul className="mt-1 grid gap-0.5">
          {item.evidence.slice(0, 2).map((e, i) => (
            <li key={i} className="text-sm">
              <span className="text-muted-foreground mr-2 font-mono text-xs">{e.source === "tool_calls" ? "tool call" : `#${e.turn}`}</span>“{e.quote}”
            </li>
          ))}
        </ul>
      )}
      {done ? (
        <div className="mt-2 flex items-center gap-2 text-sm">
          <Check className="text-ok size-4" />
          {item.answer === item.status ? <span className="text-muted-foreground">Checker was right</span> : <>Actually <Tag className={RULE_STATUS[item.answer!].tone}>{RULE_STATUS[item.answer!].label}</Tag></>}
          <Button size="xs" variant="ghost" onClick={onChange}>
            Change
          </Button>
        </div>
      ) : (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {options.map((s, i) => (
            <Button key={s} size="sm" variant="outline" disabled={pending?.phase === "sending"} aria-pressed={pending?.status === s} onClick={() => onPick(s)}>
              <kbd className="text-muted-foreground rounded border px-1 font-mono text-sm leading-4">{i + 1}</kbd>
              {i === 0 && "Right: "}
              <Tag className={RULE_STATUS[s].tone}>{RULE_STATUS[s].label}</Tag>
            </Button>
          ))}
          {pending?.phase === "sending" && <Loader2 className="text-muted-foreground size-4 animate-spin" />}
        </div>
      )}
      {pending?.phase === "error" && <ErrorBanner error={pending.error} />}
    </li>
  );
}

const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.getAttribute("role") === "combobox");
const day = (iso: string) => iso.slice(0, 10);

export function SpotChecks() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const res = useQuery(rq.spotChecks(ws));
  const [filters, setFilters] = useState<Filters>({ sort: "value" });
  const [pending, setPending] = useState<Pending>({});
  const [changing, setChanging] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<number | null>(null); // null: the first rule still to answer
  const [celebrate, setCelebrate] = useState(false);
  const [drawing, setDrawing] = useState(false);
  const d = res.data;

  const all = useMemo(() => d?.items ?? [], [d]);
  const opts = useMemo(() => {
    const uniq = (xs: [string, string][]) => [...new Map(xs).entries()].sort((a, b) => a[1].localeCompare(b[1]));
    return {
      agents: uniq(all.filter((i) => i.agent_id).map((i) => [i.agent_id!, i.agent_id!])),
      procedures: uniq(all.map((i) => [i.procedure_id, i.procedure_name ?? i.procedure_id])),
      rules: uniq(all.filter((i) => !filters.procedure || i.procedure_id === filters.procedure).map((i) => [i.rule_id, `${i.procedure_name ?? i.procedure_id}: ${ruleName(i.kind, i.index)}`])),
    };
  }, [all, filters.procedure]);
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
    return [...calls.filter((c) => c.some((i) => !i.answered)), ...calls.filter((c) => c.every((i) => i.answered))]; // calls with rules left first
  }, [shown, filters.sort]);
  const open = (i: SpotCheckItem) => !i.answered || changing.has(i.spot_check_id);
  const flat = calls.flat();
  const at = focus ?? Math.max(0, flat.findIndex(open));
  const current = flat[Math.min(at, flat.length - 1)];

  const pick = useCallback(
    async (item: SpotCheckItem, status: RuleStatus) => {
      const k = item.spot_check_id;
      setPending((p) => ({ ...p, [k]: { status, phase: "sending" } }));
      const at = flat.findIndex((x) => x.spot_check_id === k);
      const next = flat.findIndex((x, i) => i > at && open(x));
      if (next >= 0) setFocus(next);
      try {
        await review.label(ws, item.session_id, item.procedure_id, item.rule_id, { status, note: null, reviewer: author(), source: "spot_check" });
        const fresh = await qc.fetchQuery({ ...rq.spotChecks(ws), staleTime: 0 });
        setPending(({ [k]: _, ...rest }) => rest);
        setChanging((c) => (c.delete(k), new Set(c)));
        if (fresh.done && d && !d.done) setCelebrate(true);
        void qc.invalidateQueries({ queryKey: ["ws", ws, "session", item.session_id] });
      } catch (e) {
        setPending((p) => ({ ...p, [k]: { status, phase: "error", error: e } }));
      }
    },
    [ws, qc, flat, d, changing],
  );

  const draw = async (n: number) => {
    setDrawing(true);
    try {
      const fresh = await review.drawSpotChecks(ws, n);
      qc.setQueryData(rq.spotChecks(ws).queryKey, fresh);
      setCelebrate(false);
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
  }, [current]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || !current) return;
      const n = Number(e.key);
      const options = answersFor(current);
      if (e.key === "j" || e.key === "ArrowDown") setFocus(Math.min(at + 1, flat.length - 1));
      else if (e.key === "k" || e.key === "ArrowUp") setFocus(Math.max(at - 1, 0));
      else if (Number.isInteger(n) && n >= 1 && n <= options.length && open(current) && pending[current.spot_check_id]?.phase !== "sending") void pick(current, options[n - 1]);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (res.isPending)
    return (
      <div className="grid gap-3" aria-busy>
        {[0, 1, 2].map((i) => (
          <div key={i} className="bg-card grid gap-2 rounded-lg border p-4">
            <Skel className="h-4 w-1/3" />
            <Skel className="h-5 w-2/3" />
            <Skel className="h-8 w-1/2" />
          </div>
        ))}
      </div>
    );
  if (res.error || !d)
    return (
      <Banner tone="err">
        Couldn't load the spot checks. {res.error instanceof Error ? res.error.message : ""}{" "}
        <Button size="xs" variant="outline" onClick={() => void res.refetch()}>
          Try again
        </Button>
      </Banner>
    );

  if (d.done || d.size === 0)
    return (
      <div role="status" className="bg-card relative flex flex-col items-center gap-3 overflow-hidden rounded-lg border px-4 py-10 text-center">
        {celebrate && <Confetti />}
        {d.done ? (
          <>
            <PartyPopper className={cn("text-primary size-8", celebrate && "motion-safe:animate-bounce")} />
            <div className="text-lg font-semibold">
              Batch done: {d.answered}/{d.size}
            </div>
            <p className="text-muted-foreground max-w-[60ch] text-sm">
              {d.corrections ? `You found ${plural(d.corrections, "mistake the checker made", "mistakes the checker made")}. They're kept as labels and used to recalibrate it.` : "The checker got every one right."}
            </p>
          </>
        ) : (
          <>
            <Check className="text-ok size-6" />
            <div className="font-semibold">No spot checks waiting</div>
            <p className="text-muted-foreground max-w-[60ch] text-sm">Draw a batch of rules the checker passed, picked across procedures and agents, close calls first.</p>
          </>
        )}
        <MoreButton initial={d.default_batch} onDraw={(n) => void draw(n)} busy={drawing} />
      </div>
    );

  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));
  return (
    <div className="grid gap-3">
      <Progress d={d} />
      <div className="flex flex-wrap items-center gap-2">
        <FilterSelect label="Agent" value={filters.agent} options={opts.agents} onChange={(v) => set({ agent: v })} />
        <FilterSelect label="Procedure" value={filters.procedure} options={opts.procedures} onChange={(v) => set({ procedure: v, rule: undefined })} />
        <FilterSelect label="Rule" value={filters.rule} options={opts.rules} onChange={(v) => set({ rule: v })} />
        <FilterSelect<Why> label="Reason" value={filters.why} options={(Object.keys(WHY) as Why[]).map((w) => [w, WHY[w]])} onChange={(v) => set({ why: v })} />
        <label className="text-muted-foreground flex items-center gap-1 text-xs">
          From <input type="date" className="bg-background h-8 rounded-md border px-2 text-sm" value={filters.since ?? ""} onChange={(e) => set({ since: e.target.value || undefined })} />
        </label>
        <label className="text-muted-foreground flex items-center gap-1 text-xs">
          to <input type="date" className="bg-background h-8 rounded-md border px-2 text-sm" value={filters.until ?? ""} onChange={(e) => set({ until: e.target.value || undefined })} />
        </label>
        <select aria-label="Sort" className="bg-background ml-auto h-8 rounded-md border px-2 text-sm" value={filters.sort} onChange={(e) => set({ sort: e.target.value as Filters["sort"] })}>
          <option value="value">Most valuable first</option>
          <option value="newest">Newest first</option>
        </select>
      </div>
      {!calls.length && <p className="text-muted-foreground text-sm">Nothing in this batch matches the filters.</p>}
      {calls.map((items) => {
        const first = items[0];
        const qs = new URLSearchParams({ procedure: first.procedure_id, rule: first.rule_id });
        const left = items.filter(open).length;
        return (
          <article key={first.session_id} className="bg-card rounded-lg border">
            <header className="text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-1 border-b bg-[var(--band)] px-4 py-2 text-xs">
              <span className="text-foreground font-medium">{first.agent_id ?? "Call"}</span>
              <span aria-hidden>·</span>
              <HrefLink href={`${wsHref(ws, "sessions", first.session_id)}?${qs}`} title="Open the call: its transcript answers all these rules">
                Call {when(first.started_at)}
              </HrefLink>
              <span aria-hidden>·</span>
              <span className="text-k-procedure">{[...new Set(items.map((i) => i.procedure_name ?? i.procedure_id))].join(", ")}</span>
              <span className="ml-auto">{left ? `${plural(left, "rule", "rules")} to check` : "All answered"}</span>
            </header>
            <ul className="grid gap-1 p-2">
              {items.map((it) => (
                <ItemRow
                  key={it.spot_check_id}
                  item={changing.has(it.spot_check_id) ? { ...it, answered: false } : it}
                  focused={current?.spot_check_id === it.spot_check_id}
                  pending={pending[it.spot_check_id]}
                  onFocus={() => setFocus(flat.findIndex((x) => x.spot_check_id === it.spot_check_id))}
                  onPick={(s) => void pick(it, s)}
                  onChange={() => setChanging((c) => new Set(c).add(it.spot_check_id))}
                />
              ))}
            </ul>
          </article>
        );
      })}
    </div>
  );
}
