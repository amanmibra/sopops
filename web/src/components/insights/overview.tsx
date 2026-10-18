// Insights overview: KPI tiles against the prior window, what stands out, the procedure scoreboard, the outcome
// trend, what went wrong and how each agent does. Every number links to the sessions behind it.
import { useQuery } from "@tanstack/react-query";
import { Ban, CheckCircle2, ChevronDown, ChevronUp, Flag, Goal, ListChecks, Sparkles, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";
import type { AgentRow, Callout, InsightFilters, ScoreboardRow, SessionFilters, Tiles as TilesT } from "@/lib/api";
import { q } from "@/lib/api";
import { type Scope, bucketHref, drillHref, fmtDuration, fmtPct, insightsHref, priorPhrase, ruleName, sessionsHref, windowPhrase } from "@/lib/insights";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { HrefLink, useGo } from "../href-link";
import { Button } from "../ui/button";
import { ProblemsChart, TrendCharts } from "./charts";
import { DeltaMark, HeaderHint, Hint, Kpi, KpiGrid, OutcomeBar, OutcomeLegend, RateN, Spark, TipBody, WidgetCard } from "./kit";
import { AsOf, InsightsShell, Pick } from "./scope";

export const scopeOf = (f: { start: string; end: string; agent?: string | null; releases?: number[]; channel?: string | null }): Scope => ({
  start: f.start,
  end: f.end,
  agent: f.agent,
  releases: f.releases,
  channel: f.channel,
});

/** Insights queries refetch every minute, so a preset window keeps ending at "now" in a tab left open. */
export const LIVE = { refetchInterval: 60_000 } as const;

// --- tiles ---------------------------------------------------------------------------------------------------------

export function TilesRow({ tiles, scope, procedure, loading }: { tiles?: TilesT; scope?: Scope; procedure?: string; loading?: boolean }) {
  const { ws } = useWs();
  if (loading || !tiles || !scope) return <KpiGrid loading />;
  const c = tiles.current;
  const p = tiles.prior;
  const hasPrior = p.sessions_checked > 0;
  const link = (f: SessionFilters) => sessionsHref(ws, procedure ? { procedure, ...f } : f, scope);
  const outcome = (r: "followed" | "deviated"): SessionFilters => (procedure ? { procedure_result: r } : { result: r });
  const was = (v: string) => (hasPrior ? v : null);
  const n = (x: number) => x.toLocaleString();
  return (
    <KpiGrid>
      <Kpi
        label="Sessions checked"
        icon={ListChecks}
        value={n(c.sessions_checked)}
        delta={{ value: tiles.delta_pct.sessions_checked, unit: "%" }}
        prior={was(n(p.sessions_checked))}
        sub={`+${c.excluded} excluded · ${c.in_progress} in progress · ${c.failed} failed`}
        href={link({ eligible: true })}
        help="Sessions whose checks ran (checked, or partial with a result). Excluded, in-progress and failed sessions are counted, never rated."
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
        help="Sessions where every procedure check in scope was followed: each applicable step done in order, no never rule broken."
      />
      <Kpi
        label="Need attention"
        icon={TriangleAlert}
        accent="err"
        value={fmtPct(c.need_attention_pct)}
        delta={{ value: tiles.delta_points.need_attention_pct, unit: "pts", good: "down" }}
        prior={was(fmtPct(p.need_attention_pct))}
        sub={`${n(c.need_attention)} of ${n(c.sessions_checked)} sessions`}
        href={link(outcome("deviated"))}
        help="Sessions where some procedure check deviated: a step skipped or a never rule broken."
      />
      <Kpi
        label="Procedure pass rate"
        icon={Flag}
        accent="procedure"
        value={fmtPct(c.pass_rate)}
        delta={{ value: tiles.delta_points.pass_rate, unit: "pts", good: "up" }}
        prior={was(fmtPct(p.pass_rate))}
        sub={`n=${n(c.measurable_checks)} checks · rules ${fmtPct(c.rule_pass_rate)} of ${n(c.rule_n)}`}
        help="Procedure checks followed / measurable checks (one session × one procedure; not measurable ones left out). Rules: step and never results passed / judged, warning signs left out."
      />
      <Kpi
        label="Never rules broken"
        icon={Ban}
        accent="err"
        value={n(c.never_violations)}
        delta={{ value: tiles.delta_pct.never_violations, unit: "%", good: "down" }}
        prior={was(n(p.never_violations))}
        sub={`on ${n(c.never_sessions)} sessions`}
        href={link({ rule_status: ["violated"] })}
        help="Never rules the agent broke. One session can break several."
      />
      <Kpi
        label="Goal reached"
        icon={Goal}
        accent="instruction"
        value={fmtPct(c.goal_rate)}
        delta={{ value: tiles.delta_points.goal_rate, unit: "pts", good: "up" }}
        prior={was(fmtPct(p.goal_rate))}
        sub={`n=${n(c.goal_n)} checks`}
        help="Measurable procedure checks where the procedure's goal was reached, of those judged on it."
      />
    </KpiGrid>
  );
}

// --- callouts --------------------------------------------------------------------------------------------------------

export function Callouts({ callouts, scope, title = "What stands out" }: { callouts: Callout[]; scope: Scope; title?: string }) {
  const { ws } = useWs();
  if (!callouts.length) return null;
  return (
    <WidgetCard title={title} subtitle="Worked out from the numbers on this page, with the thresholds the server applies; no model involved." flush>
      <ul className="divide-y">
        {callouts.map((c) => (
          <li key={c.id} className="flex flex-wrap items-start gap-3 px-4 py-3">
            <Sparkles className={cn("mt-0.5 size-4 shrink-0", c.tone === "good" ? "text-ok" : "text-warn")} />
            <div className="min-w-[200px] flex-1">
              <div className="text-[13.5px] font-semibold">{c.title}</div>
              <div className="text-muted-foreground mt-0.5 text-[13px]">{c.body}</div>
            </div>
            <Button asChild size="xs" variant="outline">
              <HrefLink href={drillHref(ws, c.drill, scope)} className="text-foreground hover:no-underline">
                See the sessions
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
  attention: { label: "Needs attention", lead: "with the lowest pass rate first", by: (a: ScoreboardRow, b: ScoreboardRow) => (a.pass_rate ?? 101) - (b.pass_rate ?? 101) || b.deviated - a.deviated },
  volume: { label: "Most checks", lead: "with the most checks first", by: (a: ScoreboardRow, b: ScoreboardRow) => b.checks - a.checks },
  never: { label: "Most never broken", lead: "with the most never rules broken first", by: (a: ScoreboardRow, b: ScoreboardRow) => b.never_violations - a.never_violations },
} as const;
type Sort = keyof typeof SORTS;
const TOP_N = 6;

const th = "text-muted-foreground border-b px-3 py-2 text-left text-xs font-medium whitespace-nowrap";
const td = "border-b px-3 py-2.5 align-middle";

function Scoreboard({ rows, callouts, filters, scope, unit, minN }: { rows: ScoreboardRow[]; callouts: Callout[]; filters: InsightFilters; scope: Scope; unit: "day" | "week"; minN: number }) {
  const { ws } = useWs();
  const go = useGo();
  const [sort, setSort] = useState<Sort>("attention");
  const [all, setAll] = useState(false);
  const sorted = useMemo(() => [...rows].sort(SORTS[sort].by), [rows, sort]);
  const shown = all ? sorted : sorted.slice(0, TOP_N);
  const page = (pid: string) => insightsHref(ws, ["insights", "procedures", pid], { ...filters, procedure: undefined });
  return (
    <WidgetCard
      title="Procedures"
      subtitle={`${sorted.length <= TOP_N || all ? `All ${sorted.length}` : `The ${TOP_N}`} procedures ${SORTS[sort].lead}. Select a row to open it.`}
      help={`Pass rate: procedure checks followed / measurable checks, against ${priorPhrase(scope.start, scope.end)}. Most failed rules only count rules judged at least ${minN} times. The trend line follows the selected window.`}
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
      emptyTitle="No procedure checks in this window"
      emptyMessage="Sessions are checked against the procedures that apply to them once they're processed. Widen the window or clear a filter to look further back."
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[920px] border-collapse text-[13.5px]">
          <thead>
            <tr>
              <th className={th}><HeaderHint label="Procedure" tip="The procedure and how many rules it has." /></th>
              <th className={cn(th, "text-right")}><HeaderHint label="Checks" tip="Sessions this procedure applied to in the window." /></th>
              <th className={th}><HeaderHint label="Outcome split" tip="How the measurable checks ended. Hover a bar for the counts." /></th>
              <th className={cn(th, "text-right")}><HeaderHint label="Pass rate" tip="Followed / measurable checks, with the change in points against the prior window." /></th>
              <th className={th}><HeaderHint label="Most failed rule" tip={`The rule that fails most, among rules judged at least ${minN} times.`} /></th>
              <th className={cn(th, "text-right")}><HeaderHint label="Never broken" tip="Never rules broken on this procedure's checks." /></th>
              <th className={cn(th, "text-right")}><HeaderHint label="Patterns" tip="Failure patterns with findings in the window." /></th>
              <th className={th}><HeaderHint label="Trend" tip={`Pass rate per ${unit} over the selected window.`} /></th>
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
                    <div className="text-muted-foreground mt-0.5 flex items-center gap-1.5 text-xs">
                      {r.rules} rules
                      {mine.length > 0 && (
                        <Hint
                          tip={
                            <span className="grid gap-1.5">
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
                    {r.not_measurable > 0 && <div className="text-muted-foreground text-[11px]">{r.not_measurable} not measurable</div>}
                  </td>
                  <td className={td}>
                    <OutcomeBar followed={r.followed} partial={r.partial} deviated={r.deviated} />
                  </td>
                  <td className={cn(td, "text-right")}>
                    <RateN rate={r.pass_rate} n={r.measurable} thin={thin} />
                    <div className="text-xs">
                      {r.delta_points != null && r.prior_measurable > 0 ? (
                        <Hint tip={`Prior: ${fmtPct(r.prior_pass_rate)} (n=${r.prior_measurable})`} focusable={false}>
                          <DeltaMark delta={{ value: r.delta_points, unit: "pts", good: "up" }} />
                        </Hint>
                      ) : (
                        <span className="text-muted-foreground">no prior</span>
                      )}
                    </div>
                  </td>
                  <td className={cn(td, "max-w-[300px]")}>
                    {top ? (
                      <Hint tip={<TipBody head={`${ruleName(top.kind, top.index)}: failed ${top.fails} of ${top.n}`}>{top.text}</TipBody>} focusable={false}>
                        <span className="block">
                          <HrefLink
                            href={insightsHref(ws, ["insights", "procedures", r.procedure_id, "rules", top.rule_id], { ...filters, procedure: undefined })}
                            onClick={(e) => e.stopPropagation()}
                            className={cn("font-semibold", top.tone === "bad" ? "text-err" : top.tone === "warn" ? "text-warn" : "text-foreground")}
                          >
                            {ruleName(top.kind, top.index)}, {fmtPct(top.fail_rate)} fail
                          </HrefLink>{" "}
                          <span className="text-muted-foreground text-xs">n={top.n}</span>
                          <span className="text-muted-foreground line-clamp-1 text-xs">{top.text}</span>
                        </span>
                      </Hint>
                    ) : (
                      <span className="text-muted-foreground text-xs">{thin ? `under ${minN} judged` : "none failing"}</span>
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
        <div className="border-t px-4 py-1.5">
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
      subtitle="Each agent's checks, measured the same way. Most checks first; select an agent to open its sessions."
      help={`Pass rate with its n; the change needs at least ${minN} checks in both windows. Biggest gap: the rule this agent fails most, among rules it ran at least ${agentMinN} times.`}
      loading={loading}
      error={error}
      empty={!agents.length}
      emptyMessage="No checked sessions in this window."
      flush
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse text-[13.5px]">
          <thead>
            <tr>
              <th className={th}>Agent</th>
              <th className={cn(th, "text-right")}>Checks</th>
              <th className={th}>Split</th>
              <th className={cn(th, "text-right")}>Pass rate</th>
              <th className={th}>Biggest gap</th>
              <th className={cn(th, "text-right")}>
                <HeaderHint label="Median length" tip="Median session length, start to end." />
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
                <td className={cn(td, "max-w-[260px] text-[13px]")}>
                  {a.top_gap ? (
                    <Hint tip={<TipBody head={`Failed ${a.top_gap.fails} of ${a.top_gap.n}`}>{a.top_gap.text}</TipBody>} focusable={false}>
                      <HrefLink
                        href={sessionsHref(ws, { agent: a.agent ?? undefined, procedure: a.top_gap.procedure_id, rule: a.top_gap.rule_id, rule_status: a.top_gap.kind === "never" ? ["violated"] : ["skipped", "out_of_order"] }, scope)}
                        className={cn("font-semibold", a.top_gap.tone === "bad" ? "text-err" : a.top_gap.tone === "warn" ? "text-warn" : "text-foreground")}
                      >
                        {ruleName(a.top_gap.kind, a.top_gap.index)}, {fmtPct(a.top_gap.fail_rate)}
                      </HrefLink>{" "}
                      <span className="text-muted-foreground text-xs">n={a.top_gap.n}</span>
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
      title="Insights"
      subtitle={<>How sessions went against your procedures over {windowPhrase(filters)}, compared with the same length of time before. Every number opens the sessions behind it.</>}
      filters={filters}
      onFilters={onFilters}
      status={<AsOf at={res.dataUpdatedAt} fetching={res.isFetching} onRefresh={() => void res.refetch()} />}
    >
      <div className="grid gap-4">
        {res.error && !d ? (
          <WidgetCard title="Insights" {...shared} />
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
              subtitle="Each session by its worst procedure result, and the pass rate under it. Dashed lines: the first session on each release."
              help="Bars count sessions (one each, by the worst result of their procedure checks in scope). The line is followed / measurable checks per bucket, on its own 0–100% axis."
              {...shared}
              empty={!!d && d.trend.buckets.every((b) => b.n + b.not_measurable === 0)}
              emptyMessage="No checked sessions in this window."
            >
              {d && scope && <TrendCharts trend={d.trend} bucketHref={(b) => bucketHref(ws, b, scope, { eligible: true })} />}
            </WidgetCard>
            <div className="grid gap-4 lg:grid-cols-[2fr_3fr]">
              <WidgetCard
                title="What went wrong"
                subtitle="Measurable checks with each problem. One check can have several."
                help="Red problems make a check deviated. Amber ones don't: out of order or not reached make it partial, and an unheeded warning sign never changes a result but is worth a look."
                {...shared}
                empty={!!d && d.problems.every((p) => p.checks === 0)}
                emptyTitle="Nothing went wrong"
                emptyMessage="No step skipped, out of order or not reached, no never rule broken, no warning sign unheeded."
              >
                {d && scope && <ProblemsChart problems={d.problems} problemHref={(p) => sessionsHref(ws, { rule_status: p.statuses as SessionFilters["rule_status"], eligible: true }, scope)} />}
              </WidgetCard>
              <AgentsTable agents={d?.agents ?? []} scope={scope} minN={d?.thresholds.min_n ?? 10} agentMinN={d?.thresholds.agent_min_n ?? 3} loading={res.isPending} error={d ? undefined : res.error} />
            </div>
          </>
        )}
      </div>
    </InsightsShell>
  );
}
