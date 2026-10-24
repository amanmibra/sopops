// Release compare: release B against release A, by the release each session ran. A header names both sides (each
// with its own color, used on every mark on the page), the top cards show A and B side by side with the change,
// then pass rate on every release in the window, per procedure and per rule with n, 95% ranges and flags gated on min n.
import { useQuery } from "@tanstack/react-query";
import { GitCompare } from "lucide-react";
import { type ReactNode, useState } from "react";
import type { CompareRate, CompareResponse, CompareRule, InsightFilters, RateOut } from "@/lib/api";
import { q } from "@/lib/api";
import { SIDES, compareFlag, diffInterval, fmtDelta, fmtPct, insightsHref, ruleName, sessionsHref, signed, windowPhrase } from "@/lib/insights";
import { when } from "@/lib/kinds";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { RuleText, Segmented } from "../bits";
import { HrefLink } from "../href-link";
import { ReleaseTimeline } from "./charts";
import { HeaderHint, Help, Hint, Legend, OutcomeBar, OutcomeLegend, RateN, RewordedTag, Skel, StatusBar, TipBody, WidgetCard } from "./kit";
import { LIVE, scopeOf } from "./overview";
import { FlagTag } from "./procedure";
import { AsOf, InsightsShell, Pick } from "./scope";

const th = "text-muted-foreground border-b px-3 py-2 text-left text-xs font-medium whitespace-nowrap";
const td = "border-b px-3 py-2 align-middle";

type Side = keyof typeof SIDES;
type Flag = "thin" | "regressed" | "improved" | "steady";

/** "A" or "B" in its side's color: the one marker for a side, everywhere on the page. */
function SideBadge({ side, className }: { side: Side; className?: string }) {
  return <span className={cn("inline-flex size-5 shrink-0 items-center justify-center rounded text-xs font-bold", SIDES[side].badge, className)}>{SIDES[side].letter}</span>;
}

/** "A · #12": a side named with its release, never bare. */
function SideName({ side, release, className }: { side: Side; release: number | null | undefined; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 font-semibold whitespace-nowrap", className)}>
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
          <span className={cn("font-medium", compact && "text-xs")}>{word}</span>
        </span>
        {range && delta != null && (
          <span className="text-muted-foreground text-xs whitespace-nowrap">
            95% range {signed(range[0])} to {signed(range[1])}
          </span>
        )}
      </span>
    </Hint>
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
          <span className="text-muted-foreground cursor-help text-xs">
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
      <div className="mt-2 text-lg font-semibold" style={{ color: SIDES[side].color }}>
        {SIDES[side].letter} · release #{release ?? "?"}
        {r?.note && <span className="text-foreground font-normal"> {r.note}</span>}
      </div>
      <dl className="text-muted-foreground mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
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
        <table className="w-full min-w-[820px] border-collapse text-sm">
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
                  <td colSpan={5} className="bg-muted/50 border-b px-3 py-2 text-xs font-semibold">
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
                        {r.rule_change === "changed" ? (
                          <RewordedTag
                            className="ml-2"
                            releasedIn={r.reworded_in}
                            history={r.wording_history.length ? r.wording_history : r.a_text ? [{ rule_id: "a", text: r.a_text, from_release: d.a ?? null }] : []}
                            current={r.text}
                          />
                        ) : r.rule_change && (
                          <Hint focusable={false} tip={ruleChangeTip[r.rule_change as keyof typeof ruleChangeTip]}>
                            <span className={cn("ml-2 rounded-full px-2 text-xs font-medium", r.rule_change === "removed" ? "bg-err-soft text-err" : "bg-warn-soft text-warn")}>
                              {ruleChangeLabel[r.rule_change as keyof typeof ruleChangeLabel] ?? r.rule_change}
                            </span>
                          </Hint>
                        )}
                        <div className="text-muted-foreground line-clamp-2 text-sm"><RuleText text={r.text} /></div>
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
            <p className="text-muted-foreground -mt-1 inline-flex items-center gap-2 text-xs">
              <GitCompare className="size-3.5" />
              B starts as the latest release with sessions in this time range, and A as the one before it. Pick either to change it.
            </p>
            {d && ready ? (
              <div className="bg-card grid gap-px overflow-hidden rounded-lg border md:grid-cols-[1fr_1.4fr_1fr]">
                <div className="p-4">
                  <div className="text-muted-foreground text-xs">Sessions checked</div>
                  <div className="mt-1 text-xl font-semibold tabular-nums">{d.sessions_a} <span className="text-muted-foreground text-sm font-normal">on A</span> · {d.sessions_b} <span className="text-muted-foreground text-sm font-normal">on B</span></div>
                </div>
                <div className="bg-muted/30 p-4">
                  <div className="text-muted-foreground text-xs">Pass rate</div>
                  <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1 tabular-nums">
                    <span className="text-xl font-semibold">{fmtPct(ta.rate)}</span>
                    <span className="text-muted-foreground">to</span>
                    <span className="text-2xl font-semibold">{fmtPct(tb.rate)}</span>
                    <Change delta={overallDelta} range={overallDelta != null ? diffInterval(ta.passes, ta.n, tb.passes, tb.n) : null} flag={overallFlag} minN={minN} points={points} an={ta.n} bn={tb.n} compact />
                  </div>
                </div>
                <div className="p-4">
                  <div className="text-muted-foreground text-xs">Rules that changed</div>
                  <div className="mt-1 text-xl font-semibold tabular-nums"><span style={{ color: SIDES.a.color }}>{flagged.filter((r) => r.flag === "regressed").length} worse</span><span className="text-muted-foreground"> · </span><span style={{ color: SIDES.b.color }}>{flagged.filter((r) => r.flag === "improved").length} better</span></div>
                  <div className="text-muted-foreground text-xs">of {d.rules.length} rules compared</div>
                </div>
              </div>
            ) : <Skel className="h-28 rounded-lg" />}
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
                  <table className="w-full min-w-[720px] border-collapse text-sm">
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
