// The review report: headline numbers, then each procedure's themes and patterns, with the rule quoted, example
// phrasings and the sessions behind each one. Modeled on a hand-made call review.
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, CheckCircle2, Layers, ListChecks, LogOut, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import type { InsightFilters, PatternOut, RulePatternOut, RuleRef } from "@/lib/api";
import { q } from "@/lib/api";
import { type Scope, fmtPct, sessionsHref, windowPhrase } from "@/lib/insights";
import { plural, when } from "@/lib/kinds";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { RuleText, Tag } from "./bits";
import { HrefLink } from "./href-link";
import { Kpi, KpiGrid, WidgetCard } from "./insights/kit";
import { LIVE } from "./insights/overview";
import { AsOf, InsightsShell } from "./insights/scope";

/** The rule as the procedure says it. */
export function ruleQuote(rule: RuleRef | null | undefined): ReactNode {
  if (!rule?.text) return null;
  if (rule.kind === "never") return <>Your procedure says never: <RuleText text={rule.text.replace(/^never\s+/i, "")} /></>;
  if (rule.kind === "warning") return <>Warning sign: <RuleText text={rule.text} /></>;
  return <>Step {rule.index}: <RuleText text={rule.text} /></>;
}

function Examples({ p }: { p: PatternOut }) {
  const { ws } = useWs();
  if (!p.examples.length) return null;
  return (
    <ul className="mt-2 grid gap-1">
      {p.examples.slice(0, 3).map((e, i) => (
        <li key={i} className="text-[14px]">
          <HrefLink href={wsHref(ws, "sessions", e.session_id)} className="text-foreground">
            “{e.quote}”
          </HrefLink>
        </li>
      ))}
    </ul>
  );
}

function SessionChips({ p, scope }: { p: PatternOut; scope: Scope }) {
  const { ws } = useWs();
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      {p.session_ids.map((id, i) => (
        <HrefLink key={id} href={wsHref(ws, "sessions", id)} className="hover:no-underline" title="Open this session">
          <Tag className="hover:bg-brand-soft font-mono">#{i + 1}</Tag>
        </HrefLink>
      ))}
      <HrefLink href={sessionsHref(ws, { pattern: p.pattern_id }, scope)} className="text-[13px]">
        All {plural(p.sessions, "session", "sessions")}
      </HrefLink>
    </div>
  );
}

function PatternCard({ p, scope }: { p: RulePatternOut; scope: Scope }) {
  const quote = ruleQuote(p.rule);
  const subs = p.sub_patterns.filter((s) => s.findings > 0);
  return (
    <article className="border-t py-4 first:border-t-0">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <h4 className="text-[16px] font-semibold">{subs.length === 1 && subs[0].kind === "sub" ? subs[0].name : p.name}</h4>
        <span className="text-muted-foreground text-[13px] tabular-nums">
          {plural(p.sessions, "session", "sessions")}
          {p.need_attention_pct != null && ` · ${p.need_attention_pct}% of all deviated sessions`}
        </span>
      </div>
      {quote && <blockquote className="border-k-procedure/50 text-muted-foreground mt-1.5 border-l-2 pl-2.5 text-[14px]">{quote}</blockquote>}
      {subs.length > 1 || (subs.length === 1 && subs[0].kind === "other") ? (
        <div className="mt-2 grid gap-3 pl-3">
          {subs.map((s) => (
            <div key={s.pattern_id}>
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className={cn("font-semibold", s.kind === "other" && "text-muted-foreground")}>{s.name}</span>
                <span className="text-muted-foreground text-[13px] tabular-nums">
                  {plural(s.sessions, "session", "sessions")}
                  {s.need_attention_pct != null && ` · ${s.need_attention_pct}% of deviated`}
                </span>
              </div>
              {s.description && <p className="text-muted-foreground text-[13px]">{s.description}</p>}
              <Examples p={s} />
              <SessionChips p={s} scope={scope} />
            </div>
          ))}
        </div>
      ) : (
        <>
          {(subs[0]?.description ?? (p.kind === "rule" ? null : p.description)) && <p className="text-muted-foreground mt-1 text-[14px]">{subs[0]?.description ?? p.description}</p>}
          <Examples p={p} />
          <SessionChips p={p} scope={scope} />
        </>
      )}
    </article>
  );
}

export function ReportView({ filters, onFilters }: { filters: InsightFilters; onFilters: (f: InsightFilters) => void }) {
  const { ws } = useWs();
  const res = useQuery({ ...q.insightsReport(ws, filters), ...LIVE });
  const d = res.data;
  const scope: Scope | undefined = d ? { start: d.filters.start, end: d.filters.end, agent: d.filters.agent, releases: d.filters.releases, channel: d.filters.channel } : undefined;
  const h = d?.headline;
  const link = (f: Parameters<typeof sessionsHref>[1]) => (scope ? sessionsHref(ws, f, scope) : undefined);
  const shared = { loading: res.isPending, error: d ? undefined : res.error, onRetry: () => void res.refetch() };
  return (
    <InsightsShell
      view="report"
      title="Review report"
      subtitle={
        <>
          What went wrong in sessions over {windowPhrase(filters)}, grouped by where in a session it happens, with what was said.
          {d && (
            <>
              {" "}
              Covers sessions from {when(d.filters.start)} to {when(d.filters.end)}.
            </>
          )}
        </>
      }
      filters={filters}
      onFilters={onFilters}
      status={<AsOf at={res.dataUpdatedAt} fetching={res.isFetching} onRefresh={() => void res.refetch()} />}
    >
      <div className="grid max-w-[1040px] gap-4">
        {h && scope ? (
          <KpiGrid narrow>
            <Kpi label="Sessions checked" icon={ListChecks} value={h.sessions_checked} sub={`${h.not_measurable} not measurable`} href={link({ eligible: true })} help="Sessions judged against at least one procedure in this time range." />
            <Kpi label="Followed end to end" icon={CheckCircle2} accent="ok" value={fmtPct(h.followed_pct)} sub={`${h.followed} of ${h.sessions_checked}`} href={link({ result: "followed" })} help="Sessions where every procedure check came out followed: each step that applied done in order, and no never rule broken." />
            <Kpi label="Deviated" icon={TriangleAlert} accent="err" value={fmtPct(h.need_attention_pct)} sub={`${h.need_attention} of ${h.sessions_checked}`} href={link({ result: "deviated" })} help="Sessions where at least one procedure check deviated: a step skipped or a never rule broken." />
            <Kpi label="Partial" icon={ListChecks} accent="warn" value={h.partial} sub="out of order or not reached" href={link({ result: "partial" })} help="Sessions whose worst result was partial: a step out of order or not reached, and nothing worse." />
            <Kpi label="More than one problem" icon={Layers} accent="procedure" value={h.multi_problem_sessions} sub="so some patterns below share sessions" help="Sessions with findings on more than one rule." />
            <Kpi label="Excluded" icon={LogOut} value={h.excluded} sub="ended before a procedure started or finished" href={link({ status: "excluded" })} help="Sessions that ended too early to judge. They're counted here and never rated." />
          </KpiGrid>
        ) : (
          <KpiGrid loading narrow />
        )}
        {(res.isPending || res.error) && !d && <WidgetCard title="Themes" {...shared} />}
        {d && d.procedures.every((p) => !p.themes.length) && (
          <WidgetCard title="Themes" empty emptyTitle="No findings in this time range" emptyMessage="Either nothing went wrong in the sessions checked, or no sessions were checked. Pick a longer time range." />
        )}
        {d &&
          scope &&
          d.procedures
            .filter((p) => p.themes.length)
            .map((proc) => (
              <section key={proc.procedure_id} className="grid gap-3">
                {d.procedures.length > 1 && <h2 className="mt-2 text-[17px] font-semibold">{proc.procedure_name}</h2>}
                {proc.themes.map((t) => (
                  <details key={t.theme_id ?? "none"} open className="group bg-card rounded-lg border">
                    <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-3 border-b px-4 py-3 group-not-open:border-b-0">
                      <ChevronDown className="text-muted-foreground size-4 self-center transition-transform group-not-open:-rotate-90" />
                      <h3 className="text-[15px] font-semibold">{t.name}</h3>
                      <span className="text-muted-foreground text-[13px] tabular-nums">
                        {plural(t.sessions, "session", "sessions")}
                        {t.need_attention_pct != null ? ` · ${fmtPct(t.need_attention_pct)} of all deviated sessions` : ""}
                      </span>
                      {t.description && <p className="text-muted-foreground basis-full pl-7 text-[13px]">{t.description}</p>}
                    </summary>
                    <div className="px-4 pb-2">
                      {t.patterns.map((p) => (
                        <PatternCard key={p.pattern_id} p={p} scope={scope} />
                      ))}
                    </div>
                  </details>
                ))}
              </section>
            ))}
      </div>
    </InsightsShell>
  );
}
