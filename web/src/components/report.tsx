// The review report: headline numbers, then each procedure's themes and patterns, with the rule quoted, example
// phrasings and the sessions behind each one. Modeled on a hand-made call review.
import { useQuery } from "@tanstack/react-query";
import type { InsightFilters, PatternOut, RulePatternOut, RuleRef } from "@/lib/api";
import { q } from "@/lib/api";
import { type Scope, WINDOW_LABEL, fmtPct, sessionsHref } from "@/lib/insights";
import { plural, when } from "@/lib/kinds";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { ErrorBanner, Note, PageTitle, Sub, Tag } from "./bits";
import { HrefLink } from "./href-link";
import { FilterBar, InsightsNav } from "./insights";
import { Page } from "./item-shell";

/** The rule as the procedure says it. */
export function ruleQuote(rule: RuleRef | null | undefined): string | null {
  if (!rule?.text) return null;
  if (rule.kind === "never") return `Your procedure says never: ${rule.text.replace(/^never\s+/i, "")}`;
  if (rule.kind === "warning") return `Warning sign: ${rule.text}`;
  return `Step ${rule.index}: ${rule.text}`;
}

function Stat({ value, label, tone }: { value: React.ReactNode; label: string; tone?: "ok" | "err" }) {
  return (
    <div>
      <b className={cn("block text-[30px] leading-tight font-semibold tabular-nums", tone === "ok" && "text-ok", tone === "err" && "text-err")}>{value}</b>
      <span className="text-[13px]">{label}</span>
    </div>
  );
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
          {p.need_attention_pct != null && ` · ${p.need_attention_pct}% of those needing attention`}
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
                  {s.need_attention_pct != null && ` · ${s.need_attention_pct}%`}
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
  const res = useQuery(q.insightsReport(ws, filters));
  const d = res.data;
  const scope: Scope | undefined = d ? { start: d.filters.start, end: d.filters.end, agent: d.filters.agent, releases: d.filters.releases, channel: d.filters.channel } : undefined;
  const h = d?.headline;
  return (
    <Page>
      <InsightsNav here="report" filters={filters} />
      <PageTitle>Session review</PageTitle>
      <Sub>
        What went wrong across the last {WINDOW_LABEL[filters.window ?? "7d"]} of sessions, grouped by where in the conversation it happens.
        {d && (
          <>
            {" "}
            Sessions from {when(d.filters.start)} to {when(d.filters.end)}.
          </>
        )}
      </Sub>
      <FilterBar filters={filters} onChange={onFilters} />
      {res.error ? <ErrorBanner error={res.error} /> : null}
      {res.isPending && <p className="text-muted-foreground">Loading…</p>}
      {d && h && scope && (
        <>
          <div className="my-4 grid grid-cols-2 gap-6 border-y py-5 sm:grid-cols-4">
            <Stat value={h.sessions_checked} label="sessions checked" />
            <Stat value={fmtPct(h.followed_pct)} label={`followed end to end (${h.followed})`} tone="ok" />
            <Stat value={fmtPct(h.need_attention_pct)} label={`need attention (${h.need_attention})`} tone="err" />
            <Stat value={h.excluded} label="excluded (ended before a procedure)" />
          </div>
          <p className="text-muted-foreground mb-6 text-[13px]">
            {h.need_attention > 0 && (
              <>
                {plural(h.multi_problem_sessions, "session has", "sessions have")} more than one problem, so the patterns below overlap.{" "}
              </>
            )}
            {h.partial > 0 && `${plural(h.partial, "session", "sessions")} partially followed (out of order or not reached). `}
            {h.not_measurable > 0 && `${h.not_measurable} not measurable.`}
          </p>
          {d.procedures.every((p) => !p.themes.length) && <Note>No findings in this window.</Note>}
          {d.procedures
            .filter((p) => p.themes.length)
            .map((proc) => (
              <section key={proc.procedure_id} className="mb-8">
                {d.procedures.length > 1 && <h2 className="mb-2 text-[19px] font-semibold">{proc.procedure_name}</h2>}
                {proc.themes.map((t) => (
                  <details key={t.theme_id ?? "none"} open className="bg-card mb-3 rounded-xl border">
                    <summary className="flex cursor-pointer flex-wrap items-baseline gap-x-3 px-4 py-3">
                      <h3 className="text-[17px] font-semibold">{t.name}</h3>
                      <span className="text-muted-foreground text-[13px] tabular-nums">
                        {plural(t.sessions, "session", "sessions")}
                        {t.need_attention_pct != null ? ` · ${fmtPct(t.need_attention_pct)} of those needing attention` : ""}
                      </span>
                      {t.description && <p className="text-muted-foreground basis-full text-[14px]">{t.description}</p>}
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
        </>
      )}
    </Page>
  );
}
