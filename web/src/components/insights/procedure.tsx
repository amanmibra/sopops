// One procedure: tiles, what stands out, the rule ladder (every rule's results as a bar, reached, fail rate,
// release split), never rules and warning signs, the trend, what went wrong, agents and patterns by theme.
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Ban, Pencil, TriangleAlert } from "lucide-react";
import type { InsightFilters, LadderRow, PatternOut, ProcedureInsights, RulePatternOut, SessionFilters, SplitRow, ThemeOut, WarningImpact } from "@/lib/api";
import { q } from "@/lib/api";
import { FAIL_STATUSES, STATUS_MARK, type Scope, bucketHref, fmtPct, insightsHref, ruleName, sessionsHref, windowPhrase } from "@/lib/insights";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { HrefLink, useGo } from "../href-link";
import { Button } from "../ui/button";
import { ProblemsChart, TrendCharts } from "./charts";
import { DeltaMark, HeaderHint, Hint, Legend, RateN, Skel, StatusBar, TipBody, WidgetCard } from "./kit";
import { AgentsTable, Callouts, LIVE, TilesRow, scopeOf } from "./overview";
import { AsOf, ScopeBar } from "./scope";

export function failFilter(kind: string, rule: string): SessionFilters {
  return { rule, rule_status: [...(FAIL_STATUSES[kind as keyof typeof FAIL_STATUSES] ?? [])] };
}

const FLAG: Record<string, { label: string; className: string; help: (minN: number, pts: number) => string }> = {
  thin: { label: "thin", className: "text-muted-foreground", help: (n) => `Fewer than ${n} results on a side: not flagged.` },
  regressed: { label: "regressed", className: "bg-err-soft text-err border-transparent", help: (_n, p) => `At least ${p} points worse, and the 95% intervals don't overlap.` },
  improved: { label: "improved", className: "bg-brand-soft text-primary border-transparent", help: (_n, p) => `At least ${p} points better, and the 95% intervals don't overlap.` },
  steady: { label: "steady", className: "text-muted-foreground", help: () => "No clear change." },
};

export function FlagTag({ flag, minN = 10, points = 5 }: { flag: string; minN?: number; points?: number }) {
  const f = FLAG[flag] ?? FLAG.steady;
  return (
    <Hint tip={f.help(minN, points)} focusable={false}>
      <span className={cn("inline-block rounded-full border px-2 py-px text-[11px] font-medium whitespace-nowrap", f.className)}>{f.label}</span>
    </Hint>
  );
}

const SHORT = { step: "S", never: "N", warning: "W" } as const;

/** One row per rule: what happened to it on every session, how far sessions got, how often it failed. */
function Ladder({ d, filters, scope }: { d: ProcedureInsights; filters: InsightFilters; scope: Scope }) {
  const { ws } = useWs();
  const go = useGo();
  const split = new Map<string, SplitRow>((d.release_split?.rules ?? []).map((r) => [r.rule_id, r]));
  const t = d.thresholds;
  const cols = cn("grid items-center gap-x-4 px-4", d.release_split ? "md:grid-cols-[2rem_minmax(0,2.4fr)_minmax(7rem,1.3fr)_4.5rem_6.5rem_8.5rem_4.5rem]" : "md:grid-cols-[2rem_minmax(0,2.6fr)_minmax(7rem,1.4fr)_4.5rem_6.5rem_4.5rem]", "grid-cols-[2rem_minmax(0,1fr)_6.5rem]");
  const hideNarrow = "hidden md:block";
  const rulePage = (r: LadderRow) => insightsHref(ws, ["insights", "procedures", d.procedure_id, "rules", r.rule_id], filters);
  return (
    <div className="text-[13.5px]">
      <div className={cn(cols, "text-muted-foreground border-b py-2 text-xs font-medium")}>
        <span />
        <HeaderHint label="Rule" tip="Steps in order, then never rules, then warning signs." />
        <span className={hideNarrow}>
          <HeaderHint label="What happened" tip="Every judgment on this rule in the window. Hover a bar for the counts; not applicable is striped and left out of every rate." />
        </span>
        <span className={cn(hideNarrow, "text-right")}>
          <HeaderHint label="Reached" tip="Steps: judged sessions still going when the step was due. A drop between steps means sessions ending early." />
        </span>
        <span className="text-right">
          <HeaderHint label="Fail rate" tip={`Fails / (passes + fails). Colored only at n ≥ ${t.min_n}: amber from ${t.fail_warn}%, red from ${t.fail_bad}%. Warning signs: how often they came up.`} />
        </span>
        {d.release_split && (
          <span className={cn(hideNarrow, "text-right")}>
            <HeaderHint label={`#${d.release_split.a} → #${d.release_split.b}`} tip={`Pass rate on the previous release against the latest, by the release each session ran. Flagged only with n ≥ ${d.release_split.min_n} on both sides.`} />
          </span>
        )}
        <span className={cn(hideNarrow, "text-right")}>
          <HeaderHint label="Findings" tip="Failed results in the window; select to open those sessions." />
        </span>
      </div>
      {d.rules.map((r) => {
        const warn = r.kind === "warning";
        const s = split.get(r.rule_id);
        return (
          <div key={r.rule_id} role="link" tabIndex={0} onClick={() => go(rulePage(r))} onKeyDown={(e) => e.key === "Enter" && go(rulePage(r))} className={cn(cols, "hover:bg-muted/60 cursor-pointer border-b py-2.5 last:border-b-0")}>
            <span className={cn("flex size-7 items-center justify-center rounded-md text-xs font-semibold", r.kind === "step" ? "bg-brand-soft text-primary" : r.kind === "never" ? "bg-err-soft text-err" : "bg-warn-soft text-warn")}>
              {SHORT[r.kind]}
              {r.index}
            </span>
            <span className="min-w-0">
              <span className="line-clamp-2">{r.text}</span>
              <span className="mt-0.5 flex flex-wrap gap-1">
                {r.most_failed && <span className="bg-err-soft text-err rounded-full px-1.5 text-[11px] font-medium">Most failed</span>}
                {r.wordings > 1 && (
                  <Hint tip={`Worded ${r.wordings} ways in this procedure's history; results are kept by rule id.`} focusable={false}>
                    <span className="bg-warn-soft text-warn rounded-full px-1.5 text-[11px] font-medium">reworded</span>
                  </Hint>
                )}
                {r.patterns > 0 && <span className="text-muted-foreground text-[11px]">{r.patterns === 1 ? "1 pattern" : `${r.patterns} patterns`}</span>}
              </span>
            </span>
            <span className={hideNarrow}>
              <StatusBar counts={r.counts} focusable={false} />
            </span>
            <span className={cn(hideNarrow, "text-right tabular-nums")}>
              {r.reached_pct == null ? (
                <span className="text-muted-foreground">—</span>
              ) : (
                <b className={cn("font-semibold", r.reached_pct < 90 && r.n >= t.min_n && "text-warn")}>{r.reached_pct}%</b>
              )}
            </span>
            <span className="text-right">
              {warn ? (
                <Hint tip="Warning signs never count toward a pass rate: this is how often the sign came up." focusable={false}>
                  <span className="tabular-nums">
                    <b className="font-semibold">{fmtPct(r.present_rate)}</b> <span className="text-muted-foreground text-[11.5px]">n={r.n}</span>
                    <span className="text-muted-foreground block text-[11px]">came up{r.unheeded ? `, ${r.unheeded} unheeded` : ""}</span>
                  </span>
                </Hint>
              ) : (
                <RateN rate={r.fail_rate} n={r.n} thin={r.n < t.min_n} tone={r.tone} />
              )}
            </span>
            {d.release_split && (
              <span className={cn(hideNarrow, "text-right text-[13px] whitespace-nowrap tabular-nums")}>
                {s ? (
                  <Hint tip={<TipBody head={`Pass rate #${d.release_split.a}: ${fmtPct(s.a_pass_rate)} (n=${s.a_n}); #${d.release_split.b}: ${fmtPct(s.b_pass_rate)} (n=${s.b_n})`}>{s.delta_points == null ? `Under ${d.release_split.min_n} on a side: no delta.` : `${s.delta_points > 0 ? "+" : ""}${s.delta_points} points.`}</TipBody>} focusable={false}>
                    <span>
                      <span className="text-muted-foreground">{fmtPct(s.a_pass_rate)}</span> → <b className="font-semibold">{fmtPct(s.b_pass_rate)}</b>
                      <span className="block">
                        <FlagTag flag={s.flag} minN={d.release_split.min_n} points={t.flag_points} />
                      </span>
                    </span>
                  </Hint>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </span>
            )}
            <span className={cn(hideNarrow, "text-right tabular-nums")} onClick={(e) => e.stopPropagation()}>
              {r.findings ? (
                <HrefLink href={sessionsHref(ws, { procedure: d.procedure_id, ...failFilter(r.kind, r.rule_id) }, scope)} className="text-err font-semibold">
                  {r.findings}
                </HrefLink>
              ) : (
                <span className="text-muted-foreground">0</span>
              )}
            </span>
          </div>
        );
      })}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t px-4 py-2">
        <Legend items={["done", "out_of_order", "skipped", "not_reached", "not_applicable", "present", "unheeded"].map((k) => ({ label: k === "present" ? "Sign present" : k === "unheeded" ? "Sign unheeded" : STATUS_MARK[k].label, color: STATUS_MARK[k].color, striped: STATUS_MARK[k].striped }))} />
        <span className="text-muted-foreground text-xs">Select a rule for its page: by release, by agent, patterns and recent findings.</span>
      </div>
    </div>
  );
}

function NeverList({ d, scope }: { d: ProcedureInsights; scope: Scope }) {
  const { ws } = useWs();
  const nevers = d.rules.filter((r) => r.kind === "never");
  return (
    <WidgetCard title="Never rules" subtitle="What this procedure rules out, and on how many sessions the agent did it. Select one to open those sessions." help="The count is sessions where the rule was broken, of the sessions it was judged on." flush empty={!nevers.length} emptyMessage="This procedure has no never rules.">
      <ul className="divide-y">
        {nevers.map((r) => (
          <li key={r.rule_id}>
            <HrefLink href={sessionsHref(ws, { procedure: d.procedure_id, rule: r.rule_id, rule_status: ["violated"] }, scope)} className={cn("text-foreground hover:bg-muted/60 flex items-start gap-3 px-4 py-2.5 hover:no-underline", !r.fails && "pointer-events-none")}>
              <Ban className={cn("mt-0.5 size-4 shrink-0", r.fails ? "text-err" : "text-muted-foreground")} />
              <span className="min-w-0 flex-1 text-[13.5px]">{r.text}</span>
              <span className="text-right tabular-nums">
                <b className={cn("font-semibold", r.fails ? "text-err" : "text-muted-foreground")}>{r.fails ?? 0}</b>
                <span className="text-muted-foreground block text-[11px]">of {r.n}</span>
              </span>
            </HrefLink>
          </li>
        ))}
      </ul>
    </WidgetCard>
  );
}

function WarningList({ d, scope }: { d: ProcedureInsights; scope: Scope }) {
  const { ws } = useWs();
  const t = d.thresholds;
  return (
    <WidgetCard
      title="Warning signs and their effect"
      subtitle="How often each sign came up, and the pass rate without it → with it. Select one to open those sessions."
      help={`Both rates are over measurable checks that judged the sign. Flagged when the pass rate drops ${t.warning_drop_points}+ points with the sign present and both sides have n ≥ ${t.min_n}.`}
      flush
      empty={!d.warning_impact.length}
      emptyMessage="This procedure has no warning signs."
    >
      <ul className="divide-y">
        {d.warning_impact.map((w: WarningImpact) => (
          <li key={w.rule_id}>
            <HrefLink href={sessionsHref(ws, { procedure: d.procedure_id, rule: w.rule_id, rule_status: ["present", "unheeded"] }, scope)} className={cn("text-foreground hover:bg-muted/60 flex items-start gap-3 px-4 py-2.5 hover:no-underline", !w.present_n && "pointer-events-none")}>
              <TriangleAlert className={cn("mt-0.5 size-4 shrink-0", w.present_n ? "text-warn" : "text-muted-foreground")} />
              <span className="min-w-0 flex-1 text-[13.5px]">
                {w.text}
                <span className="text-muted-foreground block text-xs">
                  Came up on {w.present_n} of {w.present_n + w.absent_n}
                  {w.unheeded ? `, ${w.unheeded} unheeded` : ""}
                </span>
              </span>
              {w.present_n ? (
                <span className="text-right text-[13px] whitespace-nowrap tabular-nums">
                  <span className="text-muted-foreground">{fmtPct(w.absent_pass_rate)}</span> → <b className={cn("font-semibold", w.flag && "text-err")}>{fmtPct(w.present_pass_rate)}</b>
                  <span className="text-muted-foreground block text-[11px]">
                    n={w.absent_n} → n={w.present_n}
                    {w.delta_points != null && (
                      <>
                        {" · "}
                        <DeltaMark delta={{ value: w.delta_points, unit: "pts", good: "up" }} />
                      </>
                    )}
                  </span>
                </span>
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
            </HrefLink>
          </li>
        ))}
      </ul>
    </WidgetCard>
  );
}

function PatternLine({ p, depth, scope }: { p: PatternOut; depth: number; scope?: Scope }) {
  const { ws } = useWs();
  return (
    <div className={cn("flex items-baseline gap-2 py-0.5", depth && "pl-5 text-[13px]")}>
      <HrefLink href={sessionsHref(ws, { pattern: p.pattern_id }, scope)} className={cn(depth === 0 && "font-semibold", p.kind === "other" && "text-muted-foreground")}>
        {p.name}
      </HrefLink>
      {p.status === "retired" && <span className="text-muted-foreground rounded-full border px-1.5 text-[11px]">retired</span>}
      <span className="text-muted-foreground text-xs tabular-nums">
        {p.sessions} sessions · {p.findings} findings
      </span>
      {depth > 0 && p.description && <span className="text-muted-foreground hidden truncate text-xs lg:inline">{p.description}</span>}
    </div>
  );
}

export function PatternsByTheme({ themes, scope }: { themes: ThemeOut[]; scope?: Scope }) {
  return (
    <div className="grid gap-4">
      {themes.map((t) => (
        <div key={t.theme_id ?? "none"}>
          <div className="mb-1 flex items-baseline gap-2">
            <h3 className="font-semibold">{t.name}</h3>
            <span className="text-muted-foreground text-xs">{t.sessions} sessions</span>
          </div>
          {t.description && <p className="text-muted-foreground mb-1 text-[13px]">{t.description}</p>}
          <div className="border-l-2 pl-3">
            {t.patterns.map((p: RulePatternOut) => (
              <div key={p.pattern_id} className="mb-1.5">
                <PatternLine p={p} depth={0} scope={scope} />
                {p.rule?.text && (
                  <div className="text-muted-foreground mb-0.5 text-xs">
                    {ruleName(p.rule.kind, p.rule.index)}: {p.rule.text}
                  </div>
                )}
                {p.sub_patterns
                  .filter((s) => s.findings > 0 || s.kind === "sub")
                  .map((s) => (
                    <PatternLine key={s.pattern_id} p={s} depth={1} scope={scope} />
                  ))}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function BackBar({ href, label, filters, onFilters, status }: { href: string; label: string; filters: InsightFilters; onFilters: (f: InsightFilters) => void; status?: React.ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2 border-b pb-3">
      <div className="flex items-center gap-3">
        <Button asChild size="sm" variant="outline">
          <HrefLink href={href} className="text-foreground hover:no-underline">
            <ArrowLeft /> {label}
          </HrefLink>
        </Button>
        {status}
      </div>
      <ScopeBar filters={filters} onChange={onFilters} procedure={false} />
    </div>
  );
}

export function ProcedureInsightsView({ id, filters, onFilters }: { id: string; filters: InsightFilters; onFilters: (f: InsightFilters) => void }) {
  const { ws, cfg } = useWs();
  const res = useQuery({ ...q.insightsProcedure(ws, id, filters), ...LIVE });
  const d = res.data;
  const scope = d ? scopeOf(d.filters) : undefined;
  const proc = cfg.procedures.find((p) => p.id === id);
  const shared = { loading: res.isPending, error: d ? undefined : res.error, onRetry: () => void res.refetch() };
  const noChecks = !!d && d.tiles.current.checks === 0;
  return (
    <div className="max-w-[1320px] min-w-0">
      <BackBar href={insightsHref(ws, ["insights"], filters)} label="Insights" filters={filters} onFilters={onFilters} status={<AsOf at={res.dataUpdatedAt} fetching={res.isFetching} onRefresh={() => void res.refetch()} />} />
      <div className="grid gap-4">
        <section className="bg-card flex flex-wrap items-start justify-between gap-4 rounded-lg border px-4 py-3.5">
          {!d && res.isPending ? (
            <div className="grid w-1/2 gap-2">
              <Skel className="h-5 w-2/3" />
              <Skel className="h-3.5" />
            </div>
          ) : (
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-2">
                <h1 className="text-[20px] leading-tight font-semibold tracking-tight">{d?.procedure_name ?? proc?.name ?? id}</h1>
                <span className="text-muted-foreground text-xs">
                  {[proc?.version != null ? `v${proc.version}` : null, d ? `${d.rules.length} rules` : null, proc?.used_by.length ? `used by ${proc.used_by.length} agents` : null].filter(Boolean).join(" · ")}
                </span>
              </div>
              <p className="text-muted-foreground mt-1 text-[13px]">Each rule's results over {windowPhrase(filters)}, where sessions fail, and the patterns behind it.</p>
            </div>
          )}
          <Button asChild size="sm" variant="outline">
            <HrefLink href={wsHref(ws, "procedures", id)} className="text-foreground hover:no-underline">
              <Pencil /> Edit procedure
            </HrefLink>
          </Button>
        </section>

        {res.error && !d ? (
          <WidgetCard title="Procedure" {...shared} />
        ) : noChecks ? (
          <WidgetCard
            title="No sessions checked"
            empty
            emptyTitle="No sessions were checked against this procedure in this window"
            emptyMessage="Sessions are checked against the procedures the router finds apply to them. Widen the window or clear a filter to look further back."
            emptyAction={
              <Button asChild size="xs" variant="outline">
                <HrefLink href={sessionsHref(ws, { procedure: id })} className="text-foreground hover:no-underline">
                  All sessions it was considered for
                </HrefLink>
              </Button>
            }
          />
        ) : (
          <>
            <TilesRow tiles={d?.tiles} scope={scope} procedure={id} loading={res.isPending} />
            {d && scope && <Callouts callouts={d.callouts} scope={scope} />}
            <WidgetCard
              title="How each rule went"
              subtitle={d ? `Every rule of the procedure across ${d.tiles.current.checks} checks.` : undefined}
              help="Each rule is judged on every session the procedure applied to. Not reached and not applicable are left out of the fail rate."
              flush
              {...shared}
              empty={!!d && !d.rules.length}
              emptyMessage="No rule results in this window."
            >
              {d && scope && <Ladder d={d} filters={filters} scope={scope} />}
            </WidgetCard>
            {d && scope && (
              <div className="grid gap-4 lg:grid-cols-2">
                <NeverList d={d} scope={scope} />
                <WarningList d={d} scope={scope} />
              </div>
            )}
            <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
              <WidgetCard title="Sessions over time" subtitle="By this procedure's result; the pass rate under it. Dashed lines: the first session on each release." {...shared}>
                {d && scope && <TrendCharts trend={d.trend} bucketHref={(b) => bucketHref(ws, b, scope, { procedure: id })} />}
              </WidgetCard>
              <WidgetCard title="What went wrong" subtitle="Measurable checks with each problem; they overlap." {...shared} empty={!!d && d.problems.every((p) => !p.checks)} emptyTitle="Nothing went wrong" emptyMessage="Every measurable check was clean.">
                {d && scope && <ProblemsChart problems={d.problems} problemHref={(p) => sessionsHref(ws, { procedure: id, rule_status: p.statuses as SessionFilters["rule_status"] }, scope)} />}
              </WidgetCard>
            </div>
            <AgentsTable agents={d?.agents ?? []} scope={scope} procedure={id} minN={d?.thresholds.min_n ?? 10} agentMinN={d?.thresholds.agent_min_n ?? 3} loading={res.isPending} error={d ? undefined : res.error} />
            <WidgetCard title="Patterns by theme" subtitle="Failure patterns on this procedure's rules, grouped by where in the conversation they happen." {...shared} empty={!!d && !d.themes.length} emptyMessage="No findings yet, so no patterns.">
              {d && scope && <PatternsByTheme themes={d.themes} scope={scope} />}
            </WidgetCard>
          </>
        )}
      </div>
    </div>
  );
}
