// The Insights kit: widget cards with their loading / empty / error states, KPI tiles with deltas, hover hints,
// outcome and status bars, sparklines, legends and the chart tooltip. Every Insights page is built from these.
import { ArrowDownRight, ArrowUpRight, CircleHelp, type LucideIcon } from "lucide-react";
import { type ReactNode, useRef, useState } from "react";
import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";
import { ApiError, type WordingRef } from "@/lib/api";
import { OUTCOMES, STATUS_MARK, STATUS_ORDER, fmtDelta, fmtPct } from "@/lib/insights";
import { cn } from "@/lib/utils";
import { RuleText } from "../bits";
import { HrefLink } from "../href-link";
import { Button } from "../ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";

// --- hints ---------------------------------------------------------------------------------------------------------

/** Hover, focus or tap explanation for a value: what it counts and the numbers behind it. On touch a tap toggles
 * the tip (and doesn't follow a link or select a row underneath); a tap anywhere else closes it. */
export function Hint({ tip, children, focusable = true, className }: { tip: ReactNode; children: ReactNode; focusable?: boolean; className?: string }) {
  const [open, setOpen] = useState(false);
  const touch = useRef(false);
  return (
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger asChild>
        <span
          tabIndex={focusable ? 0 : undefined}
          className={className}
          onPointerDown={(e) => {
            touch.current = e.pointerType === "touch";
            if (touch.current) e.preventDefault();
          }}
          onClick={(e) => {
            if (!touch.current) return;
            e.preventDefault();
            e.stopPropagation();
            setOpen((o) => !o);
          }}
        >
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-80 text-left">{tip}</TooltipContent>
    </Tooltip>
  );
}

/** A tooltip body: a bold first line and an explanation under it. */
export function TipBody({ head, children }: { head?: ReactNode; children?: ReactNode }) {
  return (
    <span className="block">
      {head && <span className="block font-semibold">{head}</span>}
      {children && <span className="block opacity-80">{children}</span>}
    </span>
  );
}

/** A table header whose hover defines the column. */
export function HeaderHint({ label, tip }: { label: string; tip: ReactNode }) {
  return (
    <Hint tip={tip}>
      <span className="cursor-help border-b border-dotted border-current">{label}</span>
    </Hint>
  );
}

export function Help({ text }: { text: ReactNode }) {
  return (
    <Hint tip={text} className="text-muted-foreground hover:text-foreground inline-flex cursor-help">
      <CircleHelp className="size-3.5" aria-label="What this shows" />
    </Hint>
  );
}

// --- states --------------------------------------------------------------------------------------------------------

export function Skel({ className }: { className?: string }) {
  return <span className={cn("bg-muted block animate-pulse rounded", className)} />;
}

type CardState = {
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  /** The query worked and there is nothing to draw. */
  empty?: boolean;
  emptyTitle?: string;
  emptyMessage?: ReactNode;
  emptyAction?: ReactNode;
};

/** A widget: a titled header strip (help, an optional control) over a body that shows loading, error and empty
 * states itself, so a failed query never reads as "no activity". */
export function WidgetCard({
  title,
  subtitle,
  help,
  action,
  flush,
  className,
  bodyClassName,
  children,
  ...state
}: CardState & { title: ReactNode; subtitle?: ReactNode; help?: ReactNode; action?: ReactNode; flush?: boolean; className?: string; bodyClassName?: string; children?: ReactNode }) {
  return (
    <section className={cn("bg-card flex min-w-0 flex-col overflow-hidden rounded-lg border", className)}>
      <header className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 border-b px-4 py-2.5">
        <div className="min-w-[min(100%,16rem)] flex-1">
          <h2 className="text-[13.5px] leading-snug font-semibold">{title}</h2>
          {subtitle && <p className="text-muted-foreground mt-px text-xs leading-snug">{subtitle}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {action}
          {help && <Help text={help} />}
        </div>
      </header>
      <div className={cn(flush ? "" : "px-4 py-3.5", bodyClassName)}>
        <CardBody {...state}>{children}</CardBody>
      </div>
    </section>
  );
}

function CardBody({ loading, error, onRetry, empty, emptyTitle, emptyMessage = "No sessions in this time range.", emptyAction, children }: CardState & { children?: ReactNode }) {
  if (loading)
    return (
      <div className="grid gap-2 p-1" aria-busy>
        <Skel className="h-5" />
        <Skel className="h-5 w-11/12" />
        <Skel className="h-5 w-3/4" />
      </div>
    );
  if (error)
    return (
      <div role="alert" className="flex flex-col items-center gap-1.5 px-4 py-6 text-center">
        <div className="text-[13px] font-semibold">These numbers didn't load</div>
        <p className="text-muted-foreground max-w-[52ch] text-xs">
          The card stays empty until they do, so a failed load never reads as zero.{" "}
          {error instanceof ApiError || error instanceof Error ? <span className="font-mono">{error.message}</span> : null}
        </p>
        {onRetry && (
          <Button size="xs" variant="outline" onClick={onRetry}>
            Try again
          </Button>
        )}
      </div>
    );
  if (empty)
    return (
      <div role="status" className="text-muted-foreground flex min-h-28 flex-col items-center justify-center gap-1.5 px-4 py-5 text-center text-xs">
        {emptyTitle && <div className="text-foreground text-[13px] font-semibold">{emptyTitle}</div>}
        <div className="max-w-[60ch]">{emptyMessage}</div>
        {emptyAction}
      </div>
    );
  return <>{children}</>;
}

// --- KPI tiles -----------------------------------------------------------------------------------------------------

const ACCENT = {
  brand: "var(--primary)",
  ok: "var(--ok)",
  err: "var(--err)",
  warn: "var(--warn)",
  procedure: "var(--k-procedure)",
  instruction: "var(--k-instruction)",
} as const;

export type Delta = {
  value: number | null | undefined;
  unit: "pts" | "%";
  /** Whether a rise is good news; volume stays neutral. */
  good?: "up" | "down";
};

/** Movement against the prior window: an arrow, the delta (points for rates, % for counts), colored by whether
 * that direction is good. */
export function DeltaMark({ delta, className }: { delta: Delta; className?: string }) {
  const v = delta.value;
  if (v == null) return null;
  if (v === 0) return <span className={cn("text-muted-foreground font-semibold", className)}>no change</span>;
  const up = v > 0;
  const tone = !delta.good ? "text-muted-foreground" : (delta.good === "up") === up ? "text-ok" : "text-err";
  const Icon = up ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={cn("inline-flex items-center gap-px font-semibold whitespace-nowrap tabular-nums", tone, className)}>
      <Icon className="size-3" strokeWidth={2.5} />
      {fmtDelta(v, delta.unit).replace(/^[+−±]/, "")}
    </span>
  );
}

export function Kpi({
  label,
  value,
  icon: Icon,
  accent = "brand",
  help,
  delta,
  sub,
  prior,
  href,
}: {
  label: string;
  value: ReactNode;
  icon: LucideIcon;
  accent?: keyof typeof ACCENT;
  help: ReactNode;
  delta?: Delta;
  /** The n behind the value, and anything else that qualifies it. */
  sub?: ReactNode;
  /** The prior window's value, already formatted; null when there was nothing to compare. */
  prior?: string | null;
  href?: string;
}) {
  const body = (
    <>
      <div className="mb-1.5 flex items-start justify-between gap-2">
        <span className="text-muted-foreground inline-flex items-center gap-1 text-xs font-medium">
          {label}
          <Help text={help} />
        </span>
        <span className="flex size-7 shrink-0 items-center justify-center rounded-md" style={{ background: `color-mix(in srgb, ${ACCENT[accent]} 13%, transparent)`, color: ACCENT[accent] }}>
          <Icon className="size-3.5" strokeWidth={2.5} />
        </span>
      </div>
      <div className="text-[24px] leading-tight font-semibold tracking-tight tabular-nums">{value}</div>
      <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs">
        {delta && delta.value != null && (
          <Hint
            focusable={false}
            tip={`${delta.unit === "pts" ? "Change in percentage points" : "Change in percent"} against the same length of time just before.${delta.good ? " Green is better, red is worse." : ""}`}
          >
            <DeltaMark delta={delta} />
          </Hint>
        )}
        <span className="text-muted-foreground">{prior === undefined ? null : prior === null ? "no earlier data" : `was ${prior} before`}</span>
      </div>
      {sub && <div className="text-muted-foreground mt-0.5 text-[11.5px]">{sub}</div>}
    </>
  );
  return (
    <div className={cn("bg-card min-w-0 rounded-lg border px-4 py-3.5", href && "hover:border-ring/60 transition-colors")}>
      {href ? (
        <HrefLink href={href} className="text-foreground block hover:no-underline" aria-label={`${label}: open these sessions`}>
          {body}
        </HrefLink>
      ) : (
        body
      )}
    </div>
  );
}

export function KpiGrid({ children, loading, count = 6, narrow }: { children?: ReactNode; loading?: boolean; count?: number; narrow?: boolean }) {
  return (
    <div className={cn("grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 md:grid-cols-3", !narrow && "xl:grid-cols-6")}>
      {loading
        ? Array.from({ length: count }, (_, i) => (
            <div key={i} className="bg-card grid gap-2 rounded-lg border px-4 py-3.5" aria-busy>
              <Skel className="h-3.5 w-2/3" />
              <Skel className="h-7 w-1/2" />
              <Skel className="h-3 w-3/4" />
            </div>
          ))
        : children}
    </div>
  );
}

// --- bars ----------------------------------------------------------------------------------------------------------

/** Followed / partial / deviated as one stacked bar, the split on hover. */
export function OutcomeBar({ followed, partial, deviated, className }: { followed: number; partial: number; deviated: number; className?: string }) {
  const counts = { followed, partial, deviated };
  const total = followed + partial + deviated;
  return (
    <Hint
      tip={
        <span className="grid grid-cols-[auto_auto_1fr] items-baseline gap-x-2.5 gap-y-0.5">
          {OUTCOMES.map((o) => (
            <span key={o.key} className="contents">
              <span className="inline-flex items-center gap-1.5 font-semibold">
                <Swatch color={o.color} />
                {o.label}
              </span>
              <span className="font-semibold tabular-nums">
                {counts[o.key]} · {fmtPct(total ? Math.round((counts[o.key] / total) * 100) : null)}
              </span>
              <span className="opacity-80">{o.meaning}</span>
            </span>
          ))}
        </span>
      }
    >
      <span
        role="img"
        aria-label={`${followed} followed, ${partial} partial, ${deviated} deviated`}
        className={cn("bg-muted flex h-2 w-28 gap-px overflow-hidden rounded-full", className)}
      >
        {OUTCOMES.map((o) => (counts[o.key] ? <span key={o.key} style={{ width: `${(counts[o.key] / (total || 1)) * 100}%`, background: o.color }} /> : null))}
      </span>
    </Hint>
  );
}

/** A rule's results by status as one bar; not-applicable striped at the end (outside every rate). */
export function StatusBar({ counts, className, focusable }: { counts: Record<string, number>; className?: string; focusable?: boolean }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const shown = STATUS_ORDER.filter((s) => counts[s]);
  return (
    <Hint
      focusable={focusable}
      tip={
        total ? (
          <span className="grid grid-cols-[auto_auto_1fr] items-baseline gap-x-2.5 gap-y-0.5">
            {shown.map((s) => (
              <span key={s} className="contents">
                <span className="inline-flex items-center gap-1.5 font-semibold">
                  <Swatch color={STATUS_MARK[s].color} striped={STATUS_MARK[s].striped} />
                  {STATUS_MARK[s].label}
                </span>
                <span className="font-semibold tabular-nums">{counts[s]}</span>
                <span className="opacity-80">{STATUS_MARK[s].meaning}</span>
              </span>
            ))}
          </span>
        ) : (
          "No results in this time range."
        )
      }
    >
      <span role="img" aria-label={shown.map((s) => `${counts[s]} ${STATUS_MARK[s].label.toLowerCase()}`).join(", ") || "no results"} className={cn("bg-muted flex h-2 w-full min-w-24 gap-px overflow-hidden rounded-full", className)}>
        {shown.map((s) => (
          <span key={s} className={cn(STATUS_MARK[s].striped && "stripes")} style={{ width: `${(counts[s] / total) * 100}%`, background: STATUS_MARK[s].striped ? undefined : STATUS_MARK[s].color }} />
        ))}
      </span>
    </Hint>
  );
}

export function Swatch({ color, striped, round }: { color: string; striped?: boolean; round?: boolean }) {
  return <span aria-hidden className={cn("inline-block size-2 shrink-0", round ? "rounded-full" : "rounded-[2px]", striped && "stripes border")} style={striped ? undefined : { background: color }} />;
}

export function Legend({ items, className }: { items: { label: string; color: string; striped?: boolean; line?: boolean; tip?: ReactNode }[]; className?: string }) {
  return (
    <span className={cn("text-muted-foreground inline-flex flex-wrap items-center gap-x-3 gap-y-1 text-xs", className)}>
      {items.map((i) => {
        const body = (
          <>
            {i.line ? <span aria-hidden className="inline-block h-0.5 w-3.5 rounded" style={{ background: i.color }} /> : <Swatch color={i.color} striped={i.striped} />}
            <span className={cn(i.tip && "cursor-help border-b border-dotted border-current")}>{i.label}</span>
          </>
        );
        return i.tip ? (
          <Hint key={i.label} tip={<TipBody head={i.label}>{i.tip}</TipBody>} className="inline-flex items-center gap-1.5">
            {body}
          </Hint>
        ) : (
          <span key={i.label} className="inline-flex items-center gap-1.5">
            {body}
          </span>
        );
      })}
    </span>
  );
}

export const OutcomeLegend = () => <Legend items={OUTCOMES.map((o) => ({ label: o.label, color: o.color, tip: o.tip }))} />;

// --- sparkline -----------------------------------------------------------------------------------------------------

/** Pass rate per bucket of the selected window; hover lists every bucket with its n. */
export function Spark({ points, unit }: { points: { start: string; n: number; pass_rate: number | null }[]; unit: "day" | "week" }) {
  const data = points.map((p, i) => ({ i, v: p.pass_rate }));
  if (!points.some((p) => p.n)) return <span className="text-muted-foreground text-xs">—</span>;
  const fmt = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
  return (
    <Hint
      tip={
        <span className="block">
          <span className="mb-1 block font-semibold">Pass rate per {unit}</span>
          <span className="grid grid-cols-[auto_auto_auto] gap-x-3 tabular-nums">
            {points
              .filter((p) => p.n)
              .slice(-14)
              .map((p) => (
                <span key={p.start} className="contents">
                  <span className="opacity-80">{fmt(p.start)}</span>
                  <b>{fmtPct(p.pass_rate)}</b>
                  <span className="opacity-80">of {p.n}</span>
                </span>
              ))}
          </span>
        </span>
      }
    >
      <span className="block h-6 w-24">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 3, right: 3, bottom: 3, left: 3 }}>
            <YAxis hide domain={[0, 100]} />
            <Line type="monotone" dataKey="v" stroke="var(--primary)" strokeWidth={1.75} dot={false} connectNulls isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </span>
    </Hint>
  );
}

// --- chart tooltip ---------------------------------------------------------------------------------------------------

type TipEntry = { name?: string | number; value?: unknown; color?: string; dataKey?: unknown; payload?: unknown };

/** The compact tooltip every Insights chart renders: a label line, one row per series, an optional footer. */
export function ChartTip({
  active,
  payload,
  label,
  head,
  footer,
  format = (v) => String(v),
}: {
  active?: boolean;
  payload?: readonly TipEntry[];
  label?: unknown;
  head?: (label: string, payload: readonly TipEntry[]) => ReactNode;
  footer?: (label: string, payload: readonly TipEntry[]) => ReactNode;
  format?: (v: unknown, e: TipEntry) => ReactNode;
}) {
  if (!active || !payload?.length) return null;
  const text = String(label ?? "");
  return (
    <div className="bg-popover text-popover-foreground rounded-md border px-2.5 py-2 text-xs shadow-md">
      <div className="text-muted-foreground mb-1">{head ? head(text, payload) : text}</div>
      {payload.map((e) => (
        <div key={`${String(e.dataKey)}-${e.name}`} className="flex items-center gap-1.5">
          <Swatch color={e.color ?? "var(--muted-foreground)"} />
          <span className="text-muted-foreground">{e.name}:</span>
          <span className="font-semibold tabular-nums">{format(e.value, e)}</span>
        </div>
      ))}
      {footer && <div className="text-muted-foreground mt-1 max-w-64 text-[11px] leading-snug">{footer(text, payload)}</div>}
    </div>
  );
}

/** Shared axis styling, so every chart reads the same in light and dark. */
export const AXIS = { tick: { fontSize: 11, fill: "var(--muted-foreground)" }, tickLine: false, axisLine: false } as const;

/** A rate with its n beside it, always. Thin rates are dimmed and say so. */
export function RateN({ rate, n, thin, tone, suppressed, className }: { rate: number | null | undefined; n: number; thin?: boolean; tone?: string | null; suppressed?: number; className?: string }) {
  return (
    <span className={cn("tabular-nums whitespace-nowrap", className)}>
      <b className={cn("font-semibold", tone === "bad" && "text-err", tone === "warn" && "text-warn", thin && "text-muted-foreground")}>{fmtPct(rate)}</b>{" "}
      <span className="text-muted-foreground text-[11.5px]">of {n}</span>
      {!!suppressed && (
        <Hint tip="Findings hidden by a verdict (acceptable, or the checker was wrong) or marked incorrect; they don't count here." focusable={false}>
          <span className="text-muted-foreground ml-1 text-[11px]">{suppressed} suppressed</span>
        </Hint>
      )}
      {thin && n > 0 && (
        <Hint tip={`Only ${n} results, too few to judge. The rate gets no color and no flag until there are more.`} focusable={false}>
          <span className="text-muted-foreground ml-1 rounded border px-1 text-[10px] uppercase">too few</span>
        </Hint>
      )}
    </span>
  );
}

/** "Reworded in release N" on a rule, with its earlier wordings on hover. `history` is every wording, oldest first. */
export function RewordedTag({ releasedIn, history, current, className }: { releasedIn?: number | null; history: WordingRef[]; current?: string | null; className?: string }) {
  const earlier = history.filter((w) => w.text !== current).reverse();
  return (
    <Hint
      focusable={false}
      tip={
        <TipBody head={releasedIn != null ? `Reworded in release ${releasedIn}` : "Reworded"}>
          {earlier.length ? (
            <>
              <span className="block">{earlier.length === 1 ? "It used to say:" : "Earlier wordings, newest first:"}</span>
              {earlier.map((w) => (
                <span key={w.rule_id} className="mt-1 block">
                  “<RuleText text={w.text} />”{w.from_release != null && <span className="opacity-70"> (release {w.from_release})</span>}
                </span>
              ))}
            </>
          ) : (
            "The wording changed."
          )}
          <span className="mt-1 block">Its results, patterns and verdicts carry over from one wording to the next.</span>
        </TipBody>
      }
    >
      <span className={cn("bg-warn-soft text-warn rounded-full px-1.5 text-[11px] font-medium whitespace-nowrap", className)}>
        {releasedIn != null ? `reworded in release ${releasedIn}` : "reworded"}
      </span>
    </Hint>
  );
}
