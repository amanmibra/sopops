// Release compare: release B against release A, by the release each session ran. A header names both sides (each
// with its own color, used on every mark on the page), the top cards show A and B side by side with the change,
// then pass rate on every release in the window, per procedure and per rule with n, 95% ranges and flags gated on min n.
import { useQuery } from "@tanstack/react-query";
import { GitCompare, ListChecks, TrendingUp } from "lucide-react";
import { type ReactNode, useState } from "react";
import type { CompareRate, CompareResponse, CompareRule, InsightFilters, RateOut } from "@/lib/api";
import { q } from "@/lib/api";
import { CHECK_DEF, SIDES, compareFlag, diffInterval, fmtDelta, fmtPct, insightsHref, ruleName, sessionsHref, signed, windowPhrase } from "@/lib/insights";
import { when } from "@/lib/kinds";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { Segmented } from "../bits";
import { HrefLink } from "../href-link";
import { ReleaseTimeline } from "./charts";
import { HeaderHint, Help, Hint, Legend, OutcomeBar, OutcomeLegend, RateN, Skel, StatusBar, TipBody, WidgetCard } from "./kit";
import { LIVE, scopeOf } from "./overview";
import { FlagTag } from "./procedure";
import { AsOf, InsightsShell, Pick } from "./scope";

const th = "text-muted-foreground border-b px-3 py-2 text-left text-xs font-medium whitespace-nowrap";
const td = "border-b px-3 py-2.5 align-middle";

type Side = keyof typeof SIDES;
type Flag = "thin" | "regressed" | "improved" | "steady";

/** "A" or "B" in its side's color: the one marker for a side, everywhere on the page. */
function SideBadge({ side, className }: { side: Side; className?: string }) {
  return <span className={cn("inline-flex size-5 shrink-0 items-center justify-center rounded text-[11px] font-bold", SIDES[side].badge, className)}>{SIDES[side].letter}</span>;
}

/** "A · #12": a side named with its release, never bare. */
function SideName({ side, release, className }: { side: Side; release: number | null | undefined; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 font-semibold whitespace-nowrap", className)}>
      <SideBadge side={side} />
      <span style={{ color: SIDES[side].color }}>
        {SIDES[side].letter} · #{release ?? "?"}
      </span>
    </span>
  );
}

const flagHelp = (flag: Flag, minN: number, points: number, an: number, bn: number) =>
  flag === "thin"
    ? `Needs ${minN} or more on each release to compare. A has ${an}, B has ${bn}.`
    : flag === "steady"
      ? `The change is under ${points} points, or the two releases' 95% ranges overlap, so it could be chance. A change is only called better or worse when both tests pass, which is stricter than the range shown here.`
      : `At least ${points} points ${flag === "improved" ? "better" : "worse"}, and the two releases' 95% ranges don't overlap, so it's unlikely to be chance.`;

/** B minus A in points with its 95% range; colored only when the change is flagged, gray "no clear difference"
 * otherwise, and the reason on hover. */
function Change({ delta, range, flag, minN, points, an, bn, compact }: { delta: number | null; range: [number, number] | null; flag: Flag; minN: number; points: number; an: number; bn: number; compact?: boolean }) {
  const tone = flag === "improved" ? "text-ok" : flag === "regressed" ? "text-err" : "text-muted-foreground";
  const word = compact ? "" : flag === "improved" ? "better" : flag === "regressed" ? "worse" : flag === "thin" ? "too few to compare" : "no clear difference";
  return (
    <Hint focusable={false} tip={<TipBody head={delta == null ? "B minus A" : `B minus A: ${signed(delta)} points`}>{flagHelp(flag, minN, points, an, bn)}</TipBody>}>
      <span className={cn("inline-grid cursor-help gap-px tabular-nums", compact ? "justify-items-end text-right" : "")}>
        <span className={cn("font-semibold whitespace-nowrap", tone)}>
          {delta == null ? "" : `${signed(delta)} pts `}
          <span className={cn("font-medium", compact && "text-[11.5px]")}>{word}</span>
        </span>
        {range && delta != null && (
          <span className="text-muted-foreground text-[11px] whitespace-nowrap">
            95% range {signed(range[0])} to {signed(range[1])}
          </span>
        )}
      </span>
    </Hint>
  );
}

/** A top card: one measure for A and for B side by side, with the change under them when there is one. */
function SideBySide({ label, help, a, b, aSub, bSub, change, icon: Icon, release }: { label: string; help: ReactNode; a: ReactNode; b: ReactNode; aSub?: ReactNode; bSub?: ReactNode; change?: ReactNode; icon: typeof ListChecks; release: { a: number; b: number } }) {
  return (
    <div className="bg-card min-w-0 rounded-lg border px-4 py-3.5">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-muted-foreground inline-flex items-center gap-1 text-xs font-medium">
          {label}
          <Help text={help} />
        </span>
        <Icon className="text-muted-foreground size-3.5" />
      </div>
      <div className="grid grid-cols-2 gap-3">
        {(["a", "b"] as const).map((s) => (
          <div key={s} className="min-w-0 border-l-[3px] pl-2.5" style={{ borderColor: SIDES[s].color }}>
            <SideName side={s} release={release[s]} className="text-[11.5px]" />
            <div className="text-[22px] leading-tight font-semibold tracking-tight tabular-nums">{s === "a" ? a : b}</div>
            {(s === "a" ? aSub : bSub) && <div className="text-muted-foreground text-[11.5px]">{s === "a" ? aSub : bSub}</div>}
          </div>
        ))}
      </div>
      {change && <div className="mt-2 border-t pt-2 text-xs">{change}</div>}
    </div>
  );
}

function RuleSide({ r }: { r: RateOut }) {
  if (!r.n && !Object.keys(r.counts).length) return <span className="text-muted-foreground text-xs">not on this release</span>;
  return (
    <span className="grid gap-1">
      <RateN rate={r.pass_rate} n={r.n} thin={r.thin} />
      <StatusBar counts={r.counts} className="w-28 min-w-0" focusable={false} />
    </span>
  );
}

function ProcSide({ r, thin }: { r: CompareRate; thin: boolean }) {
  return (
    <span className="grid gap-1">
      <RateN rate={r.pass_rate} n={r.n} thin={thin} />
      <OutcomeBar followed={r.followed} partial={r.partial} deviated={r.deviated} className="w-24" />
      {r.interval && (
        <Hint focusable={false} tip="Where the true pass rate most likely sits, given how many checks there are.">
          <span className="text-muted-foreground cursor-help text-[11px]">
            95% range {r.interval[0]} to {r.interval[1]}%
          </span>
        </Hint>
      )}
    </span>
  );
}

/** One side of the header: its release, note, who published it, and (from an overview scoped to that release) the
 * agents and days its sessions came from. */
function SidePanel({ side, release, sessions, options, onPick, filters }: { side: Side; release: number | null; sessions: number; options: [string, string][]; onPick: (v?: number) => void; filters: InsightFilters }) {
  const { ws, releases } = useWs();
  const r = releases.find((x) => x.number === release);
  const info = useQuery({ ...q.insightsOverview(ws, { ...filters, release: release != null ? [release] : undefined }), enabled: release != null });
  const o = info.data;
  const used = o?.trend.buckets.filter((b) => b.n + b.not_measurable > 0) ?? [];
  const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
  const agents = (o?.agents ?? []).map((a) => a.agent ?? "Unknown agent");
  return (
    <div className="bg-card min-w-0 rounded-lg border border-l-4 px-4 py-3" style={{ borderLeftColor: SIDES[side].color }}>
      <div className="flex flex-wrap items-center gap-2">
        <SideBadge side={side} className="size-6 text-xs" />
        <span className="text-muted-foreground text-xs font-medium uppercase">{SIDES[side].word}</span>
        <Pick label={side === "a" ? "Baseline" : "Candidate"} value={release != null ? String(release) : undefined} options={options} onChange={(v) => onPick(v ? Number(v) : undefined)} className="ml-auto max-w-56" />
      </div>
      <div className="mt-2 text-[15px] font-semibold" style={{ color: SIDES[side].color }}>
        {SIDES[side].letter} · release #{release ?? "?"}
        {r?.note && <span className="text-foreground font-normal"> {r.note}</span>}
      </div>
      <dl className="text-muted-foreground mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[12.5px]">
        <dt>Sessions</dt>
        <dd className="text-foreground tabular-nums">{sessions}</dd>
        <dt>Dates</dt>
        <dd className="text-foreground">{info.isPending ? <Skel className="h-3.5 w-28" /> : used.length ? `${day(used[0].start)} to ${day(used[used.length - 1].start)}` : "none"}</dd>
        <dt>Agents</dt>
        <dd className="text-foreground min-w-0 truncate" title={agents.join(", ")}>
          {info.isPending ? <Skel className="h-3.5 w-24" /> : agents.length ? agents.join(", ") : "none"}
        </dd>
        <dt>Published</dt>
        <dd>{r ? `${when(r.created_at)}${r.author ? ` by ${r.author}` : ""}` : "unknown"}</dd>
      </dl>
    </div>
  );
}

const SHOW = { all: "All rules", flagged: "Better or worse", changed: "Reworded" } as const;
type Show = keyof typeof SHOW;

const ruleChangeLabel = { changed: "reworded", new: "new", removed: "removed" } as const;
const ruleChangeTip = {
  changed: "The wording differs between the two releases. Read both before trusting the change.",
  new: "This rule is only on B.",
  removed: "This rule is only on A.",
} as const;

function RulesTable({ d, filters }: { d: CompareResponse; filters: InsightFilters }) {
  const { ws } = useWs();
  const [show, setShow] = useState<Show>("all");
  const names = new Map(d.procedures.map((p) => [p.procedure_id, p.procedure_name]));
  const rules = d.rules.filter((r) => (show === "flagged" ? r.flag === "regressed" || r.flag === "improved" : show === "changed" ? !!r.rule_change : true));
  const byProc = [...new Set(rules.map((r) => r.procedure_id))];
  return (
    <WidgetCard
      title="Rules"
      subtitle={`Pass rate per rule on A · #${d.a} and B · #${d.b}. A change shows only with ${d.min_n} or more results on each.`}
      help="Rules are matched across releases even when reworded, so read a reworded rule's text before trusting its change. Warning signs have no pass rate and aren't compared."
      action={<Segmented small label="Show" value={show} options={Object.entries(SHOW) as [Show, string][]} onChange={setShow} />}
      flush
      empty={!rules.length}
      emptyMessage={show === "all" ? "No rule results on these releases." : "No rules match this filter."}
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[820px] border-collapse text-[13.5px]">
          <thead>
            <tr>
              <th className={th}>
                <HeaderHint label="Rule" tip="Steps and never rules of each procedure. Select a rule for its own page." />
              </th>
              <th className={th}>
                <SideName side="a" release={d.a} />
              </th>
              <th className={th}>
                <SideName side="b" release={d.b} />
              </th>
              <th className={cn(th, "text-right")}>
                <HeaderHint label="Change" tip={`B minus A, in percentage points, with its 95% range. Shown only with ${d.min_n} or more results on each release; hover a dash for the raw change.`} />
              </th>
              <th className={th}>
                <HeaderHint label="Verdict" tip={`Better or worse only when the change is at least ${d.flag_points} points and the two 95% ranges don't overlap. Too few means under ${d.min_n} results on a release.`} />
              </th>
            </tr>
          </thead>
          {byProc.map((pid) => (
            <tbody key={pid}>
              {d.procedures.length > 1 && (
                <tr>
                  <td colSpan={5} className="bg-muted/50 border-b px-3 py-1.5 text-xs font-semibold">
                    {names.get(pid) ?? pid}
                  </td>
                </tr>
              )}
              {rules
                .filter((r) => r.procedure_id === pid)
                .map((r: CompareRule) => {
                  const flag = r.flag as Flag;
                  const range = r.delta_points != null ? diffInterval(r.a.passes ?? 0, r.a.n, r.b.passes ?? 0, r.b.n) : null;
                  return (
                    <tr key={`${r.procedure_id}/${r.rule_id}`} className="hover:bg-muted/60">
                      <td className={cn(td, "max-w-[420px]")}>
                        <HrefLink href={insightsHref(ws, ["insights", "procedures", r.procedure_id, "rules", r.rule_id], filters)} className="font-semibold">
                          {ruleName(r.kind, r.index)}
                        </HrefLink>
                        {r.rule_change && (
                          <Hint focusable={false} tip={ruleChangeTip[r.rule_change as keyof typeof ruleChangeTip]}>
                            <span className={cn("ml-1.5 rounded-full px-1.5 text-[11px] font-medium", r.rule_change === "removed" ? "bg-err-soft text-err" : "bg-warn-soft text-warn")}>
                              {ruleChangeLabel[r.rule_change as keyof typeof ruleChangeLabel] ?? r.rule_change}
                            </span>
                          </Hint>
                        )}
                        <div className="text-muted-foreground line-clamp-2 text-[13px]">{r.text}</div>
                      </td>
                      <td className={cn(td, "border-l-2")} style={{ borderLeftColor: SIDES.a.color }}>
                        <RuleSide r={r.a} />
                      </td>
                      <td className={cn(td, "border-l-2")} style={{ borderLeftColor: SIDES.b.color }}>
                        <RuleSide r={r.b} />
                      </td>
                      <td className={cn(td, "text-right")}>
                        {r.delta_points != null ? (
                          <Change compact delta={r.delta_points} range={range} flag={flag} minN={d.min_n} points={d.flag_points} an={r.a.n} bn={r.b.n} />
                        ) : (
                          <Hint tip={r.raw_delta_points != null ? `${fmtDelta(r.raw_delta_points, "pts")} on too few results to judge (needs ${d.min_n} on each release).` : "One release has no rate for this rule."} focusable={false}>
                            <span className="text-muted-foreground">—</span>
                          </Hint>
                        )}
                      </td>
                      <td className={td}>
                        <FlagTag flag={flag} minN={d.min_n} points={d.flag_points} />
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          ))}
        </table>
      </div>
    </WidgetCard>
  );
}

export function CompareView({ filters, onFilters, a, b, onPick }: { filters: InsightFilters; onFilters: (f: InsightFilters) => void; a?: number; b?: number; onPick: (a?: number, b?: number) => void }) {
  const { ws, releases } = useWs();
  const res = useQuery({ ...q.insightsCompare(ws, filters, a, b), ...LIVE });
  const d = res.data;
  const scope = d ? scopeOf(d.filters) : undefined;
  const shared = { loading: res.isPending, error: d ? undefined : res.error, onRetry: () => void res.refetch() };
  const note = (n: number) => releases.find((r) => r.number === n)?.note;
  const opts = (d?.releases ?? []).map((r) => [String(r), `#${r}${note(r) ? ` ${note(r)}` : ""}`] as [string, string]);
  const ready = d && d.a != null && d.b != null;
  const flagged = d ? d.rules.filter((r) => r.flag === "regressed" || r.flag === "improved") : [];
  const total = (side: "a" | "b") => {
    const ps = d?.procedures ?? [];
    const n = ps.reduce((x, p) => x + p[side].n, 0);
    const passes = ps.reduce((x, p) => x + p[side].passes, 0);
    return { n, passes, rate: n ? Math.round((100 * passes) / n) : null };
  };
  const ta = total("a");
  const tb = total("b");
  const minN = d?.min_n ?? 10;
  const points = d?.flag_points ?? 5;
  const overallFlag = compareFlag(ta.passes, ta.n, tb.passes, tb.n, minN, points);
  const overallDelta = overallFlag !== "thin" && ta.rate != null && tb.rate != null ? tb.rate - ta.rate : null;
  const sideColor = (r: number) => (d && r === d.a ? SIDES.a.color : d && r === d.b ? SIDES.b.color : undefined);
  return (
    <InsightsShell
      view="releases"
      title="Release compare"
      subtitle={
        <>
          Pass rate on release B against release A over {windowPhrase(filters)}, counted by the release each session ran. A change is called better or worse only when both releases have
          enough results, the change is large enough, and their 95% ranges don't overlap.
        </>
      }
      filters={filters}
      onFilters={onFilters}
      scope={{ release: false }}
      status={<AsOf at={res.dataUpdatedAt} fetching={res.isFetching} onRefresh={() => void res.refetch()} />}
    >
      <div className="grid gap-4">
        {res.error && !d ? (
          <WidgetCard title="Release compare" {...shared} />
        ) : d && !ready ? (
          <WidgetCard
            title="Release compare"
            empty
            emptyTitle="Release compare needs sessions on two releases"
            emptyMessage={d.releases.length ? `Only release #${d.releases[0]} has sessions in this time range. Pick a longer time range, or pin a release per session to run two side by side.` : "No session in this time range ran a release."}
          />
        ) : (
          <>
            <div className="grid gap-3 md:grid-cols-2">
              {d && ready ? (
                <>
                  <SidePanel side="a" release={d.a} sessions={d.sessions_a} options={opts} onPick={(v) => onPick(v, b)} filters={filters} />
                  <SidePanel side="b" release={d.b} sessions={d.sessions_b} options={opts} onPick={(v) => onPick(a, v)} filters={filters} />
                </>
              ) : (
                <>
                  <Skel className="h-36 rounded-lg" />
                  <Skel className="h-36 rounded-lg" />
                </>
              )}
            </div>
            <p className="text-muted-foreground -mt-1 inline-flex items-center gap-1.5 text-xs">
              <GitCompare className="size-3.5" />
              B starts as the latest release with sessions in this time range, and A as the one before it. Pick either to change it.
            </p>
            {d && ready ? (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <SideBySide
                  label="Sessions"
                  icon={ListChecks}
                  release={{ a: d.a as number, b: d.b as number }}
                  a={d.sessions_a}
                  b={d.sessions_b}
                  aSub={`${ta.n} checks`}
                  bSub={`${tb.n} checks`}
                  help={`Checked sessions in this time range that ran each release. ${CHECK_DEF}`}
                />
                <SideBySide
                  label="Pass rate"
                  icon={TrendingUp}
                  release={{ a: d.a as number, b: d.b as number }}
                  a={fmtPct(ta.rate)}
                  b={fmtPct(tb.rate)}
                  aSub={`of ${ta.n} checks`}
                  bSub={`of ${tb.n} checks`}
                  change={<Change delta={overallDelta} range={overallDelta != null ? diffInterval(ta.passes, ta.n, tb.passes, tb.n) : null} flag={overallFlag} minN={minN} points={points} an={ta.n} bn={tb.n} />}
                  help="Followed checks out of measurable checks on each release, every procedure pooled. Under it: B minus A in percentage points, with the 95% range of that change."
                />
                <div className="bg-card min-w-0 rounded-lg border px-4 py-3.5">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="text-muted-foreground inline-flex items-center gap-1 text-xs font-medium">
                      Rules better on <SideName side="a" release={d.a} />
                      <Help text={`Steps and never rules at least ${points} points better on A than on B, with ${minN} or more results on each and 95% ranges that don't overlap.`} />
                    </span>
                  </div>
                  <div className="text-[22px] leading-tight font-semibold tabular-nums" style={{ color: SIDES.a.color }}>{flagged.filter((r) => r.flag === "regressed").length}</div>
                  <div className="text-muted-foreground text-[11.5px]">of {d.rules.length} rules compared</div>
                </div>
                <div className="bg-card min-w-0 rounded-lg border px-4 py-3.5">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="text-muted-foreground inline-flex items-center gap-1 text-xs font-medium">
                      Rules better on <SideName side="b" release={d.b} />
                      <Help text={`Steps and never rules at least ${points} points better on B than on A, with ${minN} or more results on each and 95% ranges that don't overlap.`} />
                    </span>
                  </div>
                  <div className="text-[22px] leading-tight font-semibold tabular-nums" style={{ color: SIDES.b.color }}>{flagged.filter((r) => r.flag === "improved").length}</div>
                  <div className="text-muted-foreground text-[11.5px]">
                    <Hint focusable={false} tip={`Rules with fewer than ${minN} results on a release can't be called better or worse yet.`}>
                      <span className="cursor-help border-b border-dotted border-current">{d.rules.filter((r) => r.flag === "thin").length} with too few results</span>
                    </Hint>
                  </div>
                </div>
              </div>
            ) : (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                {[0, 1, 2, 3].map((i) => (
                  <Skel key={i} className="h-32 rounded-lg" />
                ))}
              </div>
            )}
            <WidgetCard
              title="Pass rate by release"
              subtitle="Every release with sessions in this time range. Select a bar to make it B."
              help={`Followed checks out of measurable checks, counted by the release each session ran. The whisker on a bar is its 95% range. A faded bar has fewer than ${minN} checks, too few to compare.`}
              action={
                d && ready ? (
                  <Legend
                    items={[
                      { label: `A · #${d.a}`, color: SIDES.a.color, tip: "The baseline release." },
                      { label: `B · #${d.b}`, color: SIDES.b.color, tip: "The candidate release." },
                      { label: "Other releases", color: "var(--o-none)" },
                    ]}
                  />
                ) : undefined
              }
              {...shared}
              empty={!!d && !d.timeline.length}
            >
              {d && <ReleaseTimeline points={d.timeline} highlight={[d.a, d.b]} colorOf={sideColor} onPick={(r) => onPick(r === d.b ? a : d.a ?? undefined, r)} />}
            </WidgetCard>
            <WidgetCard
              title="Procedures"
              subtitle={d && ready ? `Pass rate per procedure on A · #${d.a} and B · #${d.b}, with how the checks came out.` : undefined}
              help={`Pass rate is followed checks out of measurable checks. The change is B minus A in percentage points, shown with ${minN} or more checks on each release.`}
              action={<OutcomeLegend />}
              {...shared}
              flush
              empty={!!d && !d.procedures.length}
            >
              {d && ready && (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[720px] border-collapse text-[13.5px]">
                    <thead>
                      <tr>
                        <th className={th}>
                          <HeaderHint label="Procedure" tip="Select a procedure for its own page." />
                        </th>
                        <th className={th}>
                          <SideName side="a" release={d.a} />
                        </th>
                        <th className={th}>
                          <SideName side="b" release={d.b} />
                        </th>
                        <th className={cn(th, "text-right")}>
                          <HeaderHint label="Change" tip={`B minus A, in percentage points, with its 95% range. Colored only when it's called better or worse; gray means it could be chance.`} />
                        </th>
                        <th className={th}>
                          <HeaderHint label="Verdict" tip={`Better or worse only when the change is at least ${points} points and the two 95% ranges don't overlap. Too few means under ${minN} checks on a release.`} />
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.procedures.map((p) => (
                        <tr key={p.procedure_id} className="hover:bg-muted/60">
                          <td className={td}>
                            <HrefLink href={insightsHref(ws, ["insights", "procedures", p.procedure_id], filters)} className="font-semibold">
                              {p.procedure_name}
                            </HrefLink>
                            <div className="text-xs">
                              <HrefLink href={sessionsHref(ws, { procedure: p.procedure_id, release: d.b ?? undefined }, scope)}>sessions on B · #{d.b}</HrefLink>
                            </div>
                          </td>
                          <td className={cn(td, "border-l-2")} style={{ borderLeftColor: SIDES.a.color }}>
                            <ProcSide r={p.a} thin={p.a.n < minN} />
                          </td>
                          <td className={cn(td, "border-l-2")} style={{ borderLeftColor: SIDES.b.color }}>
                            <ProcSide r={p.b} thin={p.b.n < minN} />
                          </td>
                          <td className={cn(td, "text-right")}>
                            {p.delta_points != null ? (
                              <Change compact delta={p.delta_points} range={diffInterval(p.a.passes, p.a.n, p.b.passes, p.b.n)} flag={p.flag} minN={minN} points={points} an={p.a.n} bn={p.b.n} />
                            ) : (
                              <Hint focusable={false} tip={`Too few checks to compare (needs ${minN} on each release).`}>
                                <span className="text-muted-foreground">—</span>
                              </Hint>
                            )}
                          </td>
                          <td className={td}>
                            <FlagTag flag={p.flag} minN={minN} points={points} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </WidgetCard>
            {d && ready && <RulesTable d={d} filters={filters} />}
          </>
        )}
      </div>
    </InsightsShell>
  );
}
