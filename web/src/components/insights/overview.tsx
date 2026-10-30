// Insights overview: KPI tiles against the prior window, what stands out, the procedure scoreboard, the outcome
// trend, what went wrong and how each agent does. Every number links to the sessions behind it.
import { useQuery } from "@tanstack/react-query";
import { Ban, CheckCircle2, ChevronDown, ChevronUp, Flag, Goal, ListChecks, Sparkles, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";
import type { AgentRow, Callout, InsightFilters, ScoreboardRow, SessionFilters, Tiles as TilesT } from "@/lib/api";
import { q } from "@/lib/api";
import { CHECK_DEF, type Scope, bucketHref, drillHref, fmtDuration, fmtPct, insightsHref, priorPhrase, ruleName, sessionsHref, windowPhrase } from "@/lib/insights";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { RuleText } from "../bits";
import { HrefLink, useGo } from "../href-link";
import { Button } from "../ui/button";
import { ProblemsChart, TrendCharts } from "./charts";
import { SpendCard } from "./spend";
import { DeltaMark, HeaderHint, Hint, Kpi, KpiGrid, OutcomeBar, OutcomeLegend, RateN, Spark, TipBody, WidgetCard } from "./kit";
import { AsOf, InsightsShell, Pick } from "./scope";

export const scopeOf = (f: { start: string; end: string; agent?: string | null; releases?: number[]; channel?: string | null; tags?: string[] }): Scope => ({
  start: f.start,
  end: f.end,
  agent: f.agent,
  releases: f.releases,
  channel: f.channel,
  tags: f.tags,
});

/** Insights queries refetch every minute, so a preset window keeps ending at "now" in a tab left open. */
export const LIVE = { refetchInterval: 60_000 } as const;

// --- tiles ---------------------------------------------------------------------------------------------------------

export function TilesRow({ tiles, scope, procedure, loading }: { tiles?: TilesT; scope?: Scope; procedure?: string; loading?: boolean }) {
  const { ws } = useWs();
  if (loading || !tiles || !scope) return <KpiGrid loading narrow />;
  const c = tiles.current;
  const p = tiles.prior;
  const hasPrior = p.sessions_checked > 0;
  const link = (f: SessionFilters) => sessionsHref(ws, procedure ? { procedure, ...f } : f, scope);
  const outcome = (r: "followed" | "deviated"): SessionFilters => (procedure ? { procedure_result: r } : { result: r });
  const was = (v: string) => (hasPrior ? v : null);
  const n = (x: number) => x.toLocaleString();
  return (
    <KpiGrid narrow note={hasPrior ? null : "No earlier data to compare with: the same length of time just before has no checked sessions."}>
      <Kpi
        label="Sessions checked"
        icon={ListChecks}
        value={n(c.sessions_checked)}
        delta={{ value: tiles.delta_pct.sessions_checked, unit: "%" }}
        prior={was(n(p.sessions_checked))}
        sub={`${c.excluded} excluded · ${c.in_progress} still running · ${c.failed} failed`}
        href={link({ eligible: true })}
        help="Sessions judged against at least one procedure in this time range. Excluded, still running and failed sessions are counted under the number and never rated."
      />
      <Kpi
        label="Followed end to end"
        icon={CheckCircle2}
        accent="ok"
        value={fmtPct(c.followed_pct)}
        delta={{ value: tiles.delta_points.followed_pct, unit: "pts", good: "up" }}
        prior={was(fmtPct(p.followed_pct))}
        sub={`${n(c.followed)} of ${n(c.sessions_checked)} sessions`}
        href={link(outcome("followed"))}
        help="Sessions where every procedure check came out followed: each step that applied done in order, and no never rule broken."
      />
      <Kpi
        label="Deviated"
        icon={TriangleAlert}
        accent="err"
        value={fmtPct(c.need_attention_pct)}
        delta={{ value: tiles.delta_points.need_attention_pct, unit: "pts", good: "down" }}
        prior={was(fmtPct(p.need_attention_pct))}
        sub={`${n(c.need_attention)} of ${n(c.sessions_checked)} sessions`}
        href={link(outcome("deviated"))}
        help="Sessions where at least one procedure check deviated: a step skipped or a never rule broken. These are the sessions to read first."
      />
      <Kpi
        label="Pass rate"
        icon={Flag}
        accent="procedure"
        value={fmtPct(c.pass_rate)}
        delta={{ value: tiles.delta_points.pass_rate, unit: "pts", good: "up" }}
        prior={was(fmtPct(p.pass_rate))}
        sub={`of ${n(c.measurable_checks)} checks · single rules pass ${fmtPct(c.rule_pass_rate)} of ${n(c.rule_n)}${c.suppressed ? ` · ${n(c.suppressed)} suppressed` : ""}`}
        help={`Followed checks out of measurable checks. ${CHECK_DEF} The second line counts single step and never rule results instead; warning signs never count.`}
      />
      <Kpi
        label="Never rules broken"
        icon={Ban}
        accent="err"
        value={n(c.never_violations)}
        delta={{ value: tiles.delta_pct.never_violations, unit: "%", good: "down" }}
        prior={was(n(p.never_violations))}
        sub={`in ${n(c.never_sessions)} sessions`}
        href={link({ rule_status: ["violated"] })}
        help="Times an agent broke a never rule. One session can break several."
      />
      <Kpi
        label="Goal reached"
        icon={Goal}
        accent="instruction"
        value={fmtPct(c.goal_rate)}
        delta={{ value: tiles.delta_points.goal_rate, unit: "pts", good: "up" }}
        prior={was(fmtPct(p.goal_rate))}
        sub={`of ${n(c.goal_n)} checks`}
        help="Checks where the session reached the procedure's goal, out of the measurable checks judged on a goal."
      />
    </KpiGrid>
  );
}

// --- callouts --------------------------------------------------------------------------------------------------------

export function Callouts({ callouts, scope, title = "What stands out" }: { callouts: Callout[]; scope: Scope; title?: string }) {
  const { ws } = useWs();
  if (!callouts.length) return null;
  return (
    <WidgetCard
      title={title}
      subtitle="Changes big enough to flag, found with fixed thresholds on the numbers on this page. No AI model is involved."
      help="Four things get flagged: a step or never rule that fails often, a release that moved the pass rate, a warning sign that pulls the pass rate down, and a pattern of findings that at least doubled since the period before."
      flush
    >
      <ul className="divide-y">
        {callouts.map((c) => (
          <li key={c.id} className="flex flex-wrap items-start gap-3 px-4 py-3">
            <Sparkles className={cn("mt-0.5 size-4 shrink-0", c.tone === "good" ? "text-ok" : "text-warn")} />
            <div className="min-w-[200px] flex-1">
              <div className="text-sm font-semibold">{c.title}</div>
              <div className="text-muted-foreground mt-0.5 text-sm">{c.body}</div>
            </div>
            <Button asChild size="xs" variant="outline">
              <HrefLink href={drillHref(ws, c.drill, scope)} className="text-foreground hover:no-underline">
                Open sessions
              </HrefLink>
            </Button>
          </li>
        ))}
      </ul>
    </WidgetCard>
  );
}

// --- scoreboard --------------------------------------------------------------------------------------------------------

const SORTS = {
  attention: { label: "Lowest pass rate", lead: "lowest pass rate first", by: (a: ScoreboardRow, b: ScoreboardRow) => (a.pass_rate ?? 101) - (b.pass_rate ?? 101) || b.deviated - a.deviated },
  volume: { label: "Most checks", lead: "most checks first", by: (a: ScoreboardRow, b: ScoreboardRow) => b.checks - a.checks },
  never: { label: "Most never rules broken", lead: "most never rules broken first", by: (a: ScoreboardRow, b: ScoreboardRow) => b.never_violations - a.never_violations },
} as const;
type Sort = keyof typeof SORTS;
const TOP_N = 6;

const th = "text-muted-foreground border-b px-3 py-2 text-left text-xs font-medium whitespace-nowrap";
const td = "border-b px-3 py-2 align-middle";

function Scoreboard({ rows, callouts, filters, scope, unit, minN }: { rows: ScoreboardRow[]; callouts: Callout[]; filters: InsightFilters; scope: Scope; unit: "day" | "week"; minN: number }) {
  const { ws } = useWs();
  const go = useGo();
  const [sort, setSort] = useState<Sort>("attention");
  const [all, setAll] = useState(false);
  const sorted = useMemo(() => [...rows].sort(SORTS[sort].by), [rows, sort]);
  const shown = all ? sorted : sorted.slice(0, TOP_N);
  const page = (pid: string) => insightsHref(ws, ["results", "procedures", pid], { ...filters, procedure: undefined });
  return (
    <WidgetCard
      title="Procedures"
      subtitle={`Pass rate per procedure (${sorted.length <= TOP_N || all ? `all ${sorted.length}` : `top ${TOP_N}`}, ${SORTS[sort].lead}), with the rule that fails most. Select a row to see every rule.`}
      help={`Pass rate is followed checks out of measurable checks, compared with ${priorPhrase(scope.start, scope.end)}. The most failed rule only counts rules judged at least ${minN} times. The trend line covers the selected time range.`}
      action={
        <div className="flex items-center gap-2">
          <span className="hidden md:inline-flex">
            <OutcomeLegend />
          </span>
          <Pick label="Sort" value={sort} options={Object.entries(SORTS).map(([k, v]) => [k, `Sort: ${v.label}`])} onChange={(v) => setSort((v as Sort) ?? "attention")} className="h-7 min-w-0" />
        </div>
      }
      flush
      empty={!rows.length}
      emptyTitle="No sessions were checked in this time range"
      emptyMessage="Each session is checked against the procedures that apply to it after it ends. Pick a longer time range or clear a filter."
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[920px] border-collapse text-sm">
          <thead>
            <tr>
              <th className={th}><HeaderHint label="Procedure" tip="Each procedure, with its number of rules. Select a row to see every rule." /></th>
              <th className={cn(th, "text-right")}><HeaderHint label="Checks" tip={`${CHECK_DEF} This counts sessions the procedure applied to in the time range; checks with nothing to judge are listed as not measurable.`} /></th>
              <th className={th}><HeaderHint label="Results" tip="How the measurable checks came out: followed, partial or deviated. Hover a bar for the counts." /></th>
              <th className={cn(th, "text-right")}><HeaderHint label="Pass rate" tip="Followed checks out of measurable checks. Under it, the change in percentage points from the period before." /></th>
              <th className={th}><HeaderHint label="Most failed rule" tip={`The step or never rule that fails most often, among rules judged at least ${minN} times.`} /></th>
              <th className={cn(th, "text-right")}><HeaderHint label="Never rules broken" tip="Times an agent broke one of this procedure's never rules. Select a number to open those sessions." /></th>
              <th className={cn(th, "text-right")}><HeaderHint label="Patterns" tip="Groups of similar findings on this procedure that had at least one finding in the time range. A finding is one failed result." /></th>
              <th className={th}><HeaderHint label="Trend" tip={`Pass rate per ${unit} over the selected time range. Hover a line for each ${unit}.`} /></th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const mine = callouts.filter((c) => c.procedure_id === r.procedure_id);
              const top = r.top_failing_rules[0];
              const thin = r.measurable < minN;
              return (
                <tr key={r.procedure_id} className="hover:bg-muted/60 cursor-pointer" onClick={() => go(page(r.procedure_id))}>
                  <td className={td}>
                    <HrefLink href={page(r.procedure_id)} className="text-foreground font-semibold" onClick={(e) => e.stopPropagation()}>
                      {r.procedure_name}
                    </HrefLink>
                    <div className="text-muted-foreground mt-0.5 flex flex-wrap items-center gap-x-2 text-xs whitespace-nowrap">
                      {r.rules} rules
                      {mine.length > 0 && (
                        <Hint
                          tip={
                            <span className="grid gap-2">
                              {mine.map((c) => (
                                <TipBody key={c.id} head={c.title}>
                                  {c.body}
                                </TipBody>
                              ))}
                            </span>
                          }
                        >
                          <span className="text-warn inline-flex items-center gap-1 font-medium">
                            · <Sparkles className="size-3" /> {mine.length} {mine.length === 1 ? "thing stands" : "things stand"} out
                          </span>
                        </Hint>
                      )}
                    </div>
                  </td>
                  <td className={cn(td, "text-right tabular-nums")}>
                    <HrefLink href={sessionsHref(ws, { procedure: r.procedure_id }, scope)} className="text-foreground font-semibold" onClick={(e) => e.stopPropagation()}>
                      {r.checks}
                    </HrefLink>
                    {r.not_measurable > 0 && <div className="text-muted-foreground text-xs">{r.not_measurable} not measurable</div>}
                  </td>
                  <td className={td}>
                    <OutcomeBar followed={r.followed} partial={r.partial} deviated={r.deviated} />
                  </td>
                  <td className={cn(td, "text-right whitespace-nowrap")}>
                    <RateN rate={r.pass_rate} n={r.measurable} thin={thin} suppressed={r.suppressed} />
                    <div className="text-xs">
                      {r.delta_points != null && r.prior_measurable > 0 ? (
                        <Hint tip={`Change in percentage points. The period before: ${fmtPct(r.prior_pass_rate)} of ${r.prior_measurable} checks.`} focusable={false}>
                          <DeltaMark delta={{ value: r.delta_points, unit: "pts", good: "up" }} />
                        </Hint>
                      ) : (
                        <span className="text-muted-foreground">no earlier data</span>
                      )}
                    </div>
                  </td>
                  <td className={cn(td, "max-w-[300px]")}>
                    {top ? (
                      <Hint tip={<TipBody head={`${ruleName(top.kind, top.index)}: failed ${top.fails} of ${top.n}`}><RuleText text={top.text} /></TipBody>} focusable={false}>
                        <span className="block">
                          <HrefLink
                            href={insightsHref(ws, ["results", "procedures", r.procedure_id, "rules", top.rule_id], { ...filters, procedure: undefined })}
                            onClick={(e) => e.stopPropagation()}
                            className={cn("font-semibold", top.tone === "bad" ? "text-err" : top.tone === "warn" ? "text-warn" : "text-foreground")}
                          >
                            {ruleName(top.kind, top.index)} fails {fmtPct(top.fail_rate)}
                          </HrefLink>{" "}
                          <span className="text-muted-foreground text-xs">of {top.n}</span>
                          <span className="text-muted-foreground line-clamp-1 text-xs"><RuleText text={top.text} /></span>
                        </span>
                      </Hint>
                    ) : (
                      <span className="text-muted-foreground text-xs">{thin ? `too few results (needs ${minN})` : "none failing"}</span>
                    )}
                  </td>
                  <td className={cn(td, "text-right tabular-nums")}>
                    {r.never_violations ? (
                      <HrefLink href={sessionsHref(ws, { procedure: r.procedure_id, rule_status: ["violated"] }, scope)} className="text-err font-semibold" onClick={(e) => e.stopPropagation()}>
                        {r.never_violations}
                      </HrefLink>
                    ) : (
                      <span className="text-muted-foreground">0</span>
                    )}
                  </td>
                  <td className={cn(td, "text-right tabular-nums")}>{r.open_patterns || <span className="text-muted-foreground">0</span>}</td>
                  <td className={td} onClick={(e) => e.stopPropagation()}>
                    <Spark points={r.spark} unit={unit} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {sorted.length > TOP_N && (
        <div className="border-t px-4 py-2">
          <Button size="xs" variant="ghost" onClick={() => setAll((v) => !v)}>
            {all ? <ChevronUp /> : <ChevronDown />} {all ? `Show the top ${TOP_N}` : `Show all ${sorted.length}`}
          </Button>
        </div>
      )}
    </WidgetCard>
  );
}

// --- agents ------------------------------------------------------------------------------------------------------------

export function AgentsTable({ agents, scope, procedure, minN, agentMinN, loading, error }: { agents: AgentRow[]; scope?: Scope; procedure?: string; minN: number; agentMinN: number; loading?: boolean; error?: unknown }) {
  const { ws } = useWs();
  return (
    <WidgetCard
      title="Agents"
      subtitle="Pass rate per agent, with the rule each one fails most. Agents with the most checks come first; select one to open its sessions."
      help={`The change in pass rate shows only when an agent has at least ${minN} checks now and in the period before. Biggest gap is the rule an agent fails most often, among rules it was judged on at least ${agentMinN} times.`}
      loading={loading}
      error={error}
      empty={!agents.length}
      emptyMessage="No sessions were checked in this time range."
      flush
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse text-sm">
          <thead>
            <tr>
              <th className={th}><HeaderHint label="Agent" tip="The agent that ran the sessions, and how many it ran." /></th>
              <th className={cn(th, "text-right")}><HeaderHint label="Checks" tip={`${CHECK_DEF} Only measurable checks count here.`} /></th>
              <th className={th}><HeaderHint label="Results" tip="How this agent's measurable checks came out: followed, partial or deviated. Hover a bar for the counts." /></th>
              <th className={cn(th, "text-right")}><HeaderHint label="Pass rate" tip={`Followed checks out of measurable checks. Under it, the change in percentage points from the period before (shown at ${minN} or more checks in both).`} /></th>
              <th className={th}><HeaderHint label="Biggest gap" tip={`The rule this agent fails most often, with its fail rate, among rules judged at least ${agentMinN} times. Select it to open those sessions.`} /></th>
              <th className={cn(th, "text-right")}>
                <HeaderHint label="Median length" tip="Half of this agent's sessions ran shorter than this, and half ran longer." />
              </th>
            </tr>
          </thead>
          <tbody>
            {agents.map((a) => (
              <tr key={a.agent ?? "none"} className="hover:bg-muted/60">
                <td className={td}>
                  {a.agent ? (
                    <HrefLink href={sessionsHref(ws, { agent: a.agent, ...(procedure ? { procedure } : {}), eligible: true }, scope)} className="text-foreground font-semibold">
                      {a.agent}
                    </HrefLink>
                  ) : (
                    <span className="text-muted-foreground">Unknown agent</span>
                  )}
                  <div className="text-muted-foreground text-xs">{a.sessions} sessions</div>
                </td>
                <td className={cn(td, "text-right tabular-nums")}>{a.measurable}</td>
                <td className={td}>
                  <OutcomeBar followed={a.followed} partial={a.partial} deviated={a.deviated} className="w-20" />
                </td>
                <td className={cn(td, "text-right")}>
                  <RateN rate={a.pass_rate} n={a.measurable} thin={a.thin} />
                  <div className="text-xs">
                    {a.delta_points != null ? <DeltaMark delta={{ value: a.delta_points, unit: "pts", good: "up" }} /> : <span className="text-muted-foreground">—</span>}
                  </div>
                </td>
                <td className={cn(td, "max-w-[260px] text-sm")}>
                  {a.top_gap ? (
                    <Hint tip={<TipBody head={`Failed ${a.top_gap.fails} of ${a.top_gap.n}`}><RuleText text={a.top_gap.text} /></TipBody>} focusable={false}>
                      <HrefLink
                        href={sessionsHref(ws, { agent: a.agent ?? undefined, procedure: a.top_gap.procedure_id, rule: a.top_gap.rule_id, rule_status: a.top_gap.kind === "never" ? ["violated"] : ["skipped", "out_of_order"] }, scope)}
                        className={cn("font-semibold", a.top_gap.tone === "bad" ? "text-err" : a.top_gap.tone === "warn" ? "text-warn" : "text-foreground")}
                      >
                        {ruleName(a.top_gap.kind, a.top_gap.index)} fails {fmtPct(a.top_gap.fail_rate)}
                      </HrefLink>{" "}
                      <span className="text-muted-foreground text-xs">of {a.top_gap.n}</span>
                    </Hint>
                  ) : (
                    <span className="text-muted-foreground text-xs">none</span>
                  )}
                </td>
                <td className={cn(td, "text-right tabular-nums")}>{fmtDuration(a.median_duration_s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </WidgetCard>
  );
}

// --- the page ------------------------------------------------------------------------------------------------------------

export function OverviewView({ filters, onFilters }: { filters: InsightFilters; onFilters: (f: InsightFilters) => void }) {
  const { ws } = useWs();
  const res = useQuery({ ...q.insightsOverview(ws, filters), ...LIVE });
  const d = res.data;
  const scope = d ? scopeOf(d.filters) : undefined;
  const shared = { loading: res.isPending, error: d ? undefined : res.error, onRetry: () => void res.refetch() };
  return (
    <InsightsShell
      view="overview"
      title="Results"
      subtitle={<>How sessions went against your procedures over {windowPhrase(filters)}, compared with the same length of time just before. Select a number to open the sessions behind it.</>}
      filters={filters}
      onFilters={onFilters}
      status={<AsOf at={res.dataUpdatedAt} fetching={res.isFetching} onRefresh={() => void res.refetch()} />}
    >
      <div className="grid gap-4">
        {res.error && !d ? (
          <WidgetCard title="Results" {...shared} />
        ) : (
          <>
            <TilesRow tiles={d?.tiles} scope={scope} loading={res.isPending} />
            {d && scope && <Callouts callouts={d.callouts} scope={scope} />}
            {d && scope ? (
              <Scoreboard rows={d.scoreboard} callouts={d.callouts} filters={filters} scope={scope} unit={d.trend.bucket} minN={d.thresholds.min_n} />
            ) : (
              <WidgetCard title="Procedures" {...shared} />
            )}
            <WidgetCard
              title="Sessions over time"
              subtitle="Sessions by their worst result, with the pass rate below. A dashed line marks the first session on a new release."
              help="Each bar counts sessions once, by the worst result among their procedure checks. The line below is followed checks out of measurable checks for the same day or week, on its own 0 to 100% scale."
              {...shared}
              empty={!!d && d.trend.buckets.every((b) => b.n + b.not_measurable === 0)}
              emptyMessage="No sessions were checked in this time range."
            >
              {d && scope && <TrendCharts trend={d.trend} bucketHref={(b) => bucketHref(ws, b, scope, { eligible: true })} />}
            </WidgetCard>
            <div className="grid gap-4 lg:grid-cols-[2fr_3fr]">
              <WidgetCard
                title="What went wrong"
                subtitle="How many checks had each kind of problem. One check can have several."
                help="Red problems mark a check deviated. Amber ones mark it partial (a step out of order, or a session that ended before a step). An unheeded warning sign leaves the result as it was, and it's still worth reading."
                {...shared}
                empty={!!d && d.problems.every((p) => p.checks === 0)}
                emptyTitle="Nothing went wrong"
                emptyMessage="No step skipped or out of order, no session ended before a step, no never rule broken and no warning sign unheeded."
              >
                {d && scope && <ProblemsChart problems={d.problems} problemHref={(p) => sessionsHref(ws, { rule_status: p.statuses as SessionFilters["rule_status"], eligible: true }, scope)} />}
              </WidgetCard>
              <AgentsTable agents={d?.agents ?? []} scope={scope} minN={d?.thresholds.min_n ?? 10} agentMinN={d?.thresholds.agent_min_n ?? 3} loading={res.isPending} error={d ? undefined : res.error} />
            </div>
            <SpendCard filters={filters} />
          </>
        )}
      </div>
    </InsightsShell>
  );
}
