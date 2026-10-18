// One rule: its pass rate with interval, results by status, pass rate by release and by agent, its patterns, recent
// findings with evidence, and its wordings.
import { useQuery } from "@tanstack/react-query";
import { Ban, Flag, Layers, ListX } from "lucide-react";
import type { InsightFilters, PatternOut, RateOut } from "@/lib/api";
import { q } from "@/lib/api";
import { fmtPct, insightsHref, ruleName, sessionsHref, windowPhrase } from "@/lib/insights";
import { when } from "@/lib/kinds";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { HrefLink } from "../href-link";
import { ReviewStateChip, VerdictChip, usePatternIndex } from "../review-bits";
import { RateByGroup, ReleaseTimeline } from "./charts";
import { Kpi, KpiGrid, RewordedTag, StatusBar, WidgetCard } from "./kit";
import { LineageCard } from "./lineage";
import { LIVE, scopeOf } from "./overview";
import { BackBar, failFilter } from "./procedure";
import { AsOf } from "./scope";

const mergeCounts = (rs: RateOut[]) => rs.reduce<Record<string, number>>((acc, r) => {
  for (const [k, v] of Object.entries(r.counts)) acc[k] = (acc[k] ?? 0) + v;
  return acc;
}, {});

export function RuleInsightsView({ procedureId, ruleId, filters, onFilters }: { procedureId: string; ruleId: string; filters: InsightFilters; onFilters: (f: InsightFilters) => void }) {
  const { ws, cfg } = useWs();
  const res = useQuery({ ...q.insightsRule(ws, procedureId, ruleId, filters), ...LIVE });
  const d = res.data;
  const { index } = usePatternIndex(ws);
  const reviewOf = (p: PatternOut) => {
    const x = index.get(p.pattern_id);
    const split = p.kind === "rule" && "sub_patterns" in p && (p.sub_patterns as PatternOut[]).some((s) => s.kind === "sub");
    return { verdict: x?.verdict ?? null, state: split ? null : x?.review_state, note: x?.review_note, suppressed: p.suppressed, queued: x?.review_state === "needs_review" || x?.review_state === "regressed" };
  };
  const suppressed = d?.patterns.reduce((n, p) => n + p.suppressed, 0);
  const procName = cfg.procedures.find((p) => p.id === procedureId)?.name ?? procedureId;
  const scope = d ? scopeOf(d.filters) : undefined;
  const shared = { loading: res.isPending, error: d ? undefined : res.error, onRetry: () => void res.refetch() };
  const warn = d?.kind === "warning";
  return (
    <div className="max-w-[1320px] min-w-0">
      <BackBar href={insightsHref(ws, ["insights", "procedures", procedureId], filters)} label={procName} filters={filters} onFilters={onFilters} status={<AsOf at={res.dataUpdatedAt} fetching={res.isFetching} onRefresh={() => void res.refetch()} />} />
      <div className="grid gap-4">
        <section className="bg-card rounded-lg border px-4 py-3.5">
          <div className="text-muted-foreground text-xs">
            <HrefLink href={insightsHref(ws, ["insights"], filters)}>Insights</HrefLink> / <HrefLink href={insightsHref(ws, ["insights", "procedures", procedureId], filters)}>{procName}</HrefLink>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-[20px] leading-tight font-semibold tracking-tight">{d ? ruleName(d.kind, d.index) : "Rule"}</h1>
            {d && d.wordings.length > 1 && (
              <RewordedTag
                releasedIn={d.reworded_in}
                history={[...d.wordings].sort((a, b) => (a.from_release ?? 0) - (b.from_release ?? 0)).map((w) => ({ rule_id: w.rule_id, text: w.text, from_release: w.from_release }))}
                current={d.text}
              />
            )}
          </div>
          {d?.text && <p className="mt-1 max-w-[90ch] text-[14px]">“{d.text}”</p>}
          <p className="text-muted-foreground mt-1 text-xs">Over {windowPhrase(filters)}. A reworded rule keeps its earlier results.</p>
        </section>
        {d && scope ? (
          <KpiGrid>
            {warn ? (
              <Kpi label="Came up" icon={Flag} accent="warn" value={fmtPct(d.overall.n ? Math.round((100 * ((d.overall.counts.present ?? 0) + (d.overall.counts.unheeded ?? 0))) / d.overall.n) : null)} sub={`of ${d.overall.n} results`} help="How often the sign came up, out of every session it was judged on. Warning signs have no pass rate." />
            ) : (
              <Kpi
                label="Pass rate"
                icon={Flag}
                accent="procedure"
                value={fmtPct(d.overall.pass_rate)}
                sub={`of ${d.overall.n} results${d.overall.interval ? ` · 95% range ${d.overall.interval[0]} to ${d.overall.interval[1]}%` : ""}${d.overall.thin ? ` · too few (needs ${d.thresholds.min_n})` : ""}${suppressed ? ` · ${suppressed} suppressed` : ""}`}
                help="Passed results out of passed and failed ones; not reached and not applicable never count. The 95% range is where the true rate most likely sits, given how many results there are."
              />
            )}
            <Kpi
              label={warn ? "Unheeded" : "Fails"}
              icon={Ban}
              accent="err"
              value={warn ? (d.overall.counts.unheeded ?? 0) : (d.overall.fails ?? 0)}
              sub="select to open those sessions"
              href={sessionsHref(ws, { procedure: procedureId, ...failFilter(d.kind, ruleId) }, scope)}
              help={warn ? "Sessions where the sign came up and the agent didn't act on it. Select to open them." : "Sessions where this rule failed in the time range. Select to open them."}
            />
            <Kpi label="Not counted" icon={ListX} value={d.overall.not_counted} sub="not reached or not applicable" help="Results that never count toward the rate: the session ended before the rule was due, or the rule didn't apply." />
            <Kpi label="Patterns" icon={Layers} accent="instruction" value={d.patterns.reduce((n, p) => n + p.sub_patterns.filter((s) => s.kind === "sub").length, 0)} sub="named groups of findings" help="Groups of similar findings on this rule, each with a name. A finding is one failed result." />
          </KpiGrid>
        ) : (
          <KpiGrid loading count={4} />
        )}
        {d && (
          <WidgetCard title="Results" subtitle="Every result for this rule in the time range, by status." help="Hover the bar for the count and meaning of each status.">
            <StatusBar counts={mergeCounts([d.overall])} className="h-3" />
          </WidgetCard>
        )}
        <div className="grid gap-4 lg:grid-cols-2">
          <WidgetCard
            title="By release"
            subtitle="Pass rate on each release, with its 95% range."
            help={`Counted by the release each session ran. The whisker on a bar is its 95% range. A faded bar has fewer than ${d?.thresholds.min_n ?? 10} results, too few to compare.`}
            {...shared}
            empty={!!d && (warn || !d.releases.some((r) => r.release != null && r.n))}
            emptyMessage={warn ? "Warning signs have no pass rate." : "No results on any release in this time range."}
          >
            {d && (
              <ReleaseTimeline
                sessions={false}
                highlight={d.releases.map((r) => r.release)}
                points={d.releases.filter((r) => r.release != null).map((r) => ({ release: r.release as number, sessions: r.n, n: r.n, passes: r.passes ?? 0, pass_rate: r.pass_rate, interval: r.interval, thin: r.thin }))}
              />
            )}
          </WidgetCard>
          <WidgetCard
            title="By agent"
            subtitle="Pass rate per agent. Past eight agents, the rest are pooled into Other."
            help={`Passed results out of passed and failed ones, per agent. A faded bar has fewer than ${d?.thresholds.min_n ?? 10} results, too few to judge.`}
            {...shared}
            empty={!!d && (warn || !d.agents.length)}
            emptyMessage={warn ? "Warning signs have no pass rate." : "No results in this time range."}
          >
            {d && <RateByGroup rows={d.agents.map((a) => ({ name: a.agent ?? "Unknown agent", n: a.n, passes: a.passes ?? 0, thin: a.thin }))} />}
          </WidgetCard>
        </div>
        <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
          <WidgetCard
            title="Recent findings"
            subtitle="The five latest failed results on this rule, with what was said."
            help="A finding is one failed result. Select a date to open that session."
            {...shared}
            empty={!!d && !d.recent_findings.length}
            emptyMessage="No findings in this time range."
            flush
          >
            <ul className="divide-y">
              {d?.recent_findings.map((f) => (
                <li key={f.finding_id} className="px-4 py-2.5 text-[13px]">
                  <div className="flex flex-wrap items-center gap-2">
                    <HrefLink href={wsHref(ws, "sessions", f.session_id)} className="font-semibold">
                      {when(f.started_at)}
                    </HrefLink>
                    <span className="bg-err-soft text-err rounded-full px-1.5 text-[11px] font-medium">{f.status.replace("_", " ")}</span>
                  </div>
                  {f.reason && <div className="text-muted-foreground mt-0.5">{f.reason}</div>}
                  {f.evidence.slice(0, 2).map((e, i) => (
                    <blockquote key={i} className="border-k-procedure/50 mt-1 border-l-2 pl-2.5">
                      “{e.quote}”
                    </blockquote>
                  ))}
                </li>
              ))}
            </ul>
          </WidgetCard>
          <div className="grid content-start gap-4">
            <WidgetCard
              title="Patterns"
              subtitle="Groups of similar findings on this rule, with their sessions in the time range."
              help="Select a pattern to open its sessions."
              {...shared}
              empty={!!d && !d.patterns.length}
              emptyMessage="No findings on this rule."
              flush
            >
              <ul className="divide-y">
                {d?.patterns.flatMap((p) => [p, ...p.sub_patterns.filter((s) => s.findings)]).map((p) => {
                  const r = reviewOf(p);
                  return (
                    <li key={p.pattern_id} className={cn("grid gap-0.5 px-4 py-2 text-[13px]", p.kind !== "rule" && "pl-8")}>
                      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                        <HrefLink href={wsHref(ws, "patterns", p.pattern_id)} className={cn("min-w-0", p.kind === "rule" ? "font-semibold" : p.kind === "other" && "text-muted-foreground")} title="The pattern: verdict, history and suggestions">
                          {p.name}
                        </HrefLink>
                        <span className="flex flex-wrap items-center gap-1.5">
                          <VerdictChip v={r.verdict} />
                          <ReviewStateChip state={r.state} note={r.note} />
                        </span>
                      </div>
                      <div className="text-muted-foreground flex flex-wrap gap-x-2 text-xs tabular-nums">
                        <HrefLink href={sessionsHref(ws, { pattern: p.pattern_id }, scope)} className="text-inherit">
                          {p.sessions} sessions · {p.findings} findings
                        </HrefLink>
                        {!!r.suppressed && <span>{r.suppressed} suppressed</span>}
                        {r.queued && !!r.state && (
                          <HrefLink href={`${wsHref(ws, "patterns")}?procedure=${encodeURIComponent(procedureId)}`} className="font-medium">
                            Review
                          </HrefLink>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </WidgetCard>
            {d && <LineageCard procedureId={procedureId} ruleId={d.rule_id} kind={d.kind} filters={filters} />}
          </div>
        </div>
      </div>
    </div>
  );
}
