// One rule: its pass rate with interval, results by status, pass rate by release and by agent, its patterns, recent
// findings with evidence, and its wordings.
import { useQuery } from "@tanstack/react-query";
import { Ban, Flag, Layers, ListX } from "lucide-react";
import type { InsightFilters, RateOut } from "@/lib/api";
import { q } from "@/lib/api";
import { fmtPct, insightsHref, ruleName, sessionsHref, windowPhrase } from "@/lib/insights";
import { when } from "@/lib/kinds";
import { useWs, wsHref } from "@/lib/workspace";
import { HrefLink } from "../href-link";
import { RateByGroup, ReleaseTimeline } from "./charts";
import { Kpi, KpiGrid, StatusBar, WidgetCard } from "./kit";
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
          <h1 className="mt-1 text-[20px] leading-tight font-semibold tracking-tight">{d ? ruleName(d.kind, d.index) : "Rule"}</h1>
          {d?.text && <p className="mt-1 max-w-[90ch] text-[14px]">“{d.text}”</p>}
          <p className="text-muted-foreground mt-1 text-xs">Over {windowPhrase(filters)}. Results are kept by the rule's id, so a reworded rule keeps its history.</p>
        </section>
        {d && scope ? (
          <KpiGrid>
            {warn ? (
              <Kpi label="Came up" icon={Flag} accent="warn" value={fmtPct(d.overall.n ? Math.round((100 * ((d.overall.counts.present ?? 0) + (d.overall.counts.unheeded ?? 0))) / d.overall.n) : null)} sub={`n=${d.overall.n} judged`} help="Warning signs have no pass rate: this is how often the sign was present." />
            ) : (
              <Kpi
                label="Pass rate"
                icon={Flag}
                accent="procedure"
                value={fmtPct(d.overall.pass_rate)}
                sub={`n=${d.overall.n}${d.overall.interval ? ` · 95%: ${d.overall.interval[0]}–${d.overall.interval[1]}%` : ""}${d.overall.thin ? " · thin" : ""}`}
                help="Passes / (passes + fails); not reached and not applicable left out. The interval is a 95% Wilson interval."
              />
            )}
            <Kpi
              label={warn ? "Unheeded" : "Fails"}
              icon={Ban}
              accent="err"
              value={warn ? (d.overall.counts.unheeded ?? 0) : (d.overall.fails ?? 0)}
              sub="select to open those sessions"
              href={sessionsHref(ws, { procedure: procedureId, ...failFilter(d.kind, ruleId) }, scope)}
              help="Sessions where this rule failed, in the window."
            />
            <Kpi label="Not counted" icon={ListX} value={d.overall.not_counted} sub="not reached or not applicable" help="Judgments left out of the rate: the session ended before the rule was due, or it didn't apply." />
            <Kpi label="Patterns" icon={Layers} accent="instruction" value={d.patterns.reduce((n, p) => n + p.sub_patterns.filter((s) => s.kind === "sub").length, 0)} sub="named failure modes" help="Named failure modes clustered from this rule's findings." />
          </KpiGrid>
        ) : (
          <KpiGrid loading count={4} />
        )}
        {d && (
          <WidgetCard title="What happened" subtitle="Every judgment on this rule in the window." help="Hover the bar for the counts by status.">
            <StatusBar counts={mergeCounts([d.overall])} className="h-3" />
          </WidgetCard>
        )}
        <div className="grid gap-4 lg:grid-cols-2">
          <WidgetCard
            title="By release"
            subtitle="Pass rate on each release, by the release each session ran, with its 95% interval."
            help={`Faded bars are thin (n < ${d?.thresholds.min_n ?? 10}).`}
            {...shared}
            empty={!!d && (warn || !d.releases.some((r) => r.release != null && r.n))}
            emptyMessage={warn ? "Warning signs have no pass rate." : "No results on a release in this window."}
          >
            {d && (
              <ReleaseTimeline
                sessions={false}
                highlight={d.releases.map((r) => r.release)}
                points={d.releases.filter((r) => r.release != null).map((r) => ({ release: r.release as number, sessions: r.n, n: r.n, passes: r.passes ?? 0, pass_rate: r.pass_rate, interval: r.interval, thin: r.thin }))}
              />
            )}
          </WidgetCard>
          <WidgetCard title="By agent" subtitle="Pass rate per agent, with n. Past eight agents the rest pool into Other." {...shared} empty={!!d && (warn || !d.agents.length)} emptyMessage={warn ? "Warning signs have no pass rate." : "No results in this window."}>
            {d && <RateByGroup rows={d.agents.map((a) => ({ name: a.agent ?? "Unknown agent", n: a.n, passes: a.passes ?? 0, thin: a.thin }))} />}
          </WidgetCard>
        </div>
        <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
          <WidgetCard title="Recent findings" subtitle="The five latest failed results, with what was said." {...shared} empty={!!d && !d.recent_findings.length} emptyMessage="None in this window." flush>
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
            <WidgetCard title="Patterns" subtitle="This rule's failure patterns, with sessions in the window." {...shared} empty={!!d && !d.patterns.length} emptyMessage="No findings on this rule." flush>
              <ul className="divide-y">
                {d?.patterns.flatMap((p) => [p, ...p.sub_patterns.filter((s) => s.findings)]).map((p) => (
                  <li key={p.pattern_id} className="flex items-baseline justify-between gap-3 px-4 py-2 text-[13px]">
                    <HrefLink href={sessionsHref(ws, { pattern: p.pattern_id }, scope)} className={p.kind === "rule" ? "font-semibold" : p.kind === "other" ? "text-muted-foreground pl-4" : "pl-4"}>
                      {p.name}
                    </HrefLink>
                    <span className="text-muted-foreground text-xs whitespace-nowrap tabular-nums">
                      {p.sessions} sessions · {p.findings} findings
                    </span>
                  </li>
                ))}
              </ul>
            </WidgetCard>
            {d && d.wordings.length > 1 && (
              <WidgetCard title="Wordings" subtitle="This rule has been reworded; the newest first." flush>
                <ul className="divide-y">
                  {d.wordings.map((w) => (
                    <li key={w.text_hash} className="px-4 py-2 text-[13px]">
                      “{w.text}”
                      <div className="text-muted-foreground text-xs">
                        {w.results} results, last {when(w.last_seen)}
                      </div>
                    </li>
                  ))}
                </ul>
              </WidgetCard>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
