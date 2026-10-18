// Release compare: release B against release A, by the release each session ran. The releases side by side, pass
// rate on every release in the window, then per procedure and per rule with n, intervals and flags gated on min n.
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, GitCompare, ListChecks, TrendingDown, TrendingUp } from "lucide-react";
import { useState } from "react";
import type { CompareRate, CompareResponse, CompareRule, InsightFilters, RateOut } from "@/lib/api";
import { q } from "@/lib/api";
import { fmtDelta, fmtPct, insightsHref, ruleName, sessionsHref, windowPhrase } from "@/lib/insights";
import { when } from "@/lib/kinds";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { Segmented } from "../bits";
import { HrefLink } from "../href-link";
import { ReleaseTimeline } from "./charts";
import { DeltaMark, HeaderHint, Hint, Kpi, KpiGrid, OutcomeBar, OutcomeLegend, RateN, StatusBar, WidgetCard } from "./kit";
import { LIVE, scopeOf } from "./overview";
import { FlagTag } from "./procedure";
import { AsOf, InsightsShell, Pick } from "./scope";

const th = "text-muted-foreground border-b px-3 py-2 text-left text-xs font-medium whitespace-nowrap";
const td = "border-b px-3 py-2.5 align-middle";

function Side({ r, thin }: { r: CompareRate; thin: boolean }) {
  return (
    <span className="grid gap-1">
      <RateN rate={r.pass_rate} n={r.n} thin={thin} />
      <OutcomeBar followed={r.followed} partial={r.partial} deviated={r.deviated} className="w-24" />
      {r.interval && <span className="text-muted-foreground text-[11px]">95%: {r.interval[0]}–{r.interval[1]}%</span>}
    </span>
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

function Releases({ d }: { d: CompareResponse }) {
  const { releases } = useWs();
  const rows = [d.a, d.b].map((n, i) => ({ side: i ? "B" : "A", r: releases.find((x) => x.number === n), n, sessions: i ? d.sessions_b : d.sessions_a }));
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] border-collapse text-[13.5px]">
        <thead>
          <tr>
            <th className={th} />
            <th className={th}>Release</th>
            <th className={th}>Note</th>
            <th className={th}>Published</th>
            <th className={cn(th, "text-right")}>Sessions in window</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((x) => (
            <tr key={x.side}>
              <td className={cn(td, "w-10")}>
                <span className={cn("flex size-6 items-center justify-center rounded-md text-xs font-bold", x.side === "B" ? "bg-primary text-primary-foreground" : "bg-muted")}>{x.side}</span>
              </td>
              <td className={cn(td, "font-semibold")}>#{x.n}</td>
              <td className={cn(td, "text-muted-foreground")}>{x.r?.note ?? "—"}</td>
              <td className={cn(td, "text-muted-foreground")}>{x.r ? `${when(x.r.created_at)}${x.r.author ? ` by ${x.r.author}` : ""}` : "—"}</td>
              <td className={cn(td, "text-right tabular-nums")}>{x.sessions}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const SHOW = { all: "All rules", flagged: "Flagged", changed: "Rule changed" } as const;
type Show = keyof typeof SHOW;

function RulesTable({ d, filters }: { d: CompareResponse; filters: InsightFilters }) {
  const { ws } = useWs();
  const [show, setShow] = useState<Show>("all");
  const names = new Map(d.procedures.map((p) => [p.procedure_id, p.procedure_name]));
  const rules = d.rules.filter((r) => (show === "flagged" ? r.flag === "regressed" || r.flag === "improved" : show === "changed" ? !!r.rule_change : true));
  const byProc = [...new Set(rules.map((r) => r.procedure_id))];
  return (
    <WidgetCard
      title="Rules"
      subtitle={`Each rule's pass rate on #${d.a} and #${d.b}. Δ and flags need n ≥ ${d.min_n} on both sides.`}
      help="A rule is matched across releases by its id. A reworded rule is still compared; check its wording before reading the change. Warning signs have no pass rate and aren't compared."
      action={<Segmented small label="Show" value={show} options={Object.entries(SHOW) as [Show, string][]} onChange={setShow} />}
      flush
      empty={!rules.length}
      emptyMessage={show === "all" ? "No rule results on these releases." : "None in this comparison."}
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[820px] border-collapse text-[13.5px]">
          <thead>
            <tr>
              <th className={th}>Rule</th>
              <th className={th}>A #{d.a}</th>
              <th className={th}>B #{d.b}</th>
              <th className={cn(th, "text-right")}>
                <HeaderHint label="Δ" tip={`B minus A, in points. Shown only with n ≥ ${d.min_n} on both sides; hover a dash for the raw change.`} />
              </th>
              <th className={th}>Flag</th>
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
                .map((r: CompareRule) => (
                  <tr key={`${r.procedure_id}/${r.rule_id}`} className="hover:bg-muted/60">
                    <td className={cn(td, "max-w-[420px]")}>
                      <HrefLink href={insightsHref(ws, ["insights", "procedures", r.procedure_id, "rules", r.rule_id], filters)} className="font-semibold">
                        {ruleName(r.kind, r.index)}
                      </HrefLink>
                      {r.rule_change && (
                        <span className={cn("ml-1.5 rounded-full px-1.5 text-[11px] font-medium", r.rule_change === "removed" ? "bg-err-soft text-err" : "bg-warn-soft text-warn")}>
                          {r.rule_change === "changed" ? "rule changed" : r.rule_change}
                        </span>
                      )}
                      <div className="text-muted-foreground line-clamp-2 text-[13px]">{r.text}</div>
                    </td>
                    <td className={td}>
                      <RuleSide r={r.a} />
                    </td>
                    <td className={td}>
                      <RuleSide r={r.b} />
                    </td>
                    <td className={cn(td, "text-right")}>
                      {r.delta_points != null ? (
                        <DeltaMark delta={{ value: r.delta_points, unit: "pts", good: r.flag === "steady" ? undefined : "up" }} />
                      ) : (
                        <Hint tip={r.raw_delta_points != null ? `${fmtDelta(r.raw_delta_points, "pts")} on too few results to read` : "No rate on one side."} focusable={false}>
                          <span className="text-muted-foreground">—</span>
                        </Hint>
                      )}
                    </td>
                    <td className={td}>
                      <FlagTag flag={r.flag} minN={d.min_n} points={d.flag_points} />
                    </td>
                  </tr>
                ))}
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
    return { n, rate: n ? Math.round((100 * passes) / n) : null };
  };
  const ta = total("a");
  const tb = total("b");
  return (
    <InsightsShell
      view="releases"
      title="Release compare"
      subtitle={<>Pass rates of a candidate release against a baseline over {windowPhrase(filters)}, by the release each session ran (never by date). A change is flagged only when both sides have enough results, it's big enough, and the 95% intervals don't overlap.</>}
      filters={filters}
      onFilters={onFilters}
      scope={{ release: false }}
      status={<AsOf at={res.dataUpdatedAt} fetching={res.isFetching} onRefresh={() => void res.refetch()} />}
    >
      <div className="grid gap-4">
        <div className="bg-card flex flex-wrap items-center gap-2 rounded-lg border px-4 py-2.5 text-[13px]">
          <GitCompare className="text-muted-foreground size-4" />
          <span className="font-semibold">A</span>
          <Pick label="Baseline" value={d?.a != null ? String(d.a) : undefined} options={opts} onChange={(v) => onPick(v ? Number(v) : undefined, b)} className="max-w-64" />
          <ArrowRight className="text-muted-foreground size-4" />
          <span className="font-semibold">B</span>
          <Pick label="Candidate" value={d?.b != null ? String(d.b) : undefined} options={opts} onChange={(v) => onPick(a, v ? Number(v) : undefined)} className="max-w-64" />
          <span className="text-muted-foreground ml-auto text-xs">Default: the latest release with sessions in the window, against the one before it.</span>
        </div>
        {res.error && !d ? (
          <WidgetCard title="Release compare" {...shared} />
        ) : d && !ready ? (
          <WidgetCard
            title="Release compare"
            empty
            emptyTitle="Comparing needs sessions on two releases"
            emptyMessage={d.releases.length ? `Only release #${d.releases[0]} has sessions in this window. Widen the window, or pin a release per call to run both side by side.` : "No session in this window ran a release."}
          />
        ) : (
          <>
            {d && ready ? (
              <KpiGrid>
                <Kpi label={`Sessions on #${d.a}`} icon={ListChecks} value={d.sessions_a} sub="baseline (A)" help="Sessions in the window that ran release A." />
                <Kpi label={`Sessions on #${d.b}`} icon={ListChecks} accent="procedure" value={d.sessions_b} sub="candidate (B)" help="Sessions in the window that ran release B." />
                <Kpi label="Pass rate on A" icon={TrendingDown} value={fmtPct(ta.rate)} sub={`n=${ta.n} checks`} help="Procedure checks followed / measurable checks, on release A." />
                <Kpi
                  label="Pass rate on B"
                  icon={TrendingUp}
                  accent="ok"
                  value={fmtPct(tb.rate)}
                  delta={{ value: ta.n >= d.min_n && tb.n >= d.min_n && ta.rate != null && tb.rate != null ? tb.rate - ta.rate : null, unit: "pts", good: "up" }}
                  sub={`n=${tb.n} checks${ta.n < d.min_n || tb.n < d.min_n ? ` · under ${d.min_n} on a side: no delta` : " · vs A"}`}
                  help="Procedure checks followed / measurable checks, on release B; the change against A in points."
                />
                <Kpi label="Rules regressed" icon={TrendingDown} accent="err" value={flagged.filter((r) => r.flag === "regressed").length} sub={`of ${d.rules.length} compared`} help={`At least ${d.flag_points} points worse with n ≥ ${d.min_n} on both sides and non-overlapping 95% intervals.`} />
                <Kpi label="Rules improved" icon={TrendingUp} accent="ok" value={flagged.filter((r) => r.flag === "improved").length} sub={`${d.rules.filter((r) => r.flag === "thin").length} too thin to judge`} help={`At least ${d.flag_points} points better with n ≥ ${d.min_n} on both sides and non-overlapping 95% intervals.`} />
              </KpiGrid>
            ) : (
              <KpiGrid loading />
            )}
            <div className="grid gap-4 lg:grid-cols-[2fr_3fr]">
              <WidgetCard title="Releases compared" subtitle="Select a bar on the right to make it the candidate." {...shared} flush>
                {d && ready && <Releases d={d} />}
              </WidgetCard>
              <WidgetCard title="Pass rate by release" subtitle="Every release with sessions in the window; A and B highlighted. Whiskers: 95% interval." help={`Faded bars are thin (n < ${d?.min_n ?? 10}).`} {...shared} empty={!!d && !d.timeline.length}>
                {d && <ReleaseTimeline points={d.timeline} highlight={[d.a, d.b]} onPick={(r) => onPick(r === d.b ? a : d.a ?? undefined, r)} />}
              </WidgetCard>
            </div>
            <WidgetCard title="Procedures" subtitle={d && ready ? `Each procedure on #${d.a} and #${d.b}: pass rate with n, the outcome split and the 95% interval.` : undefined} action={<OutcomeLegend />} {...shared} flush empty={!!d && !d.procedures.length}>
              {d && ready && (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[720px] border-collapse text-[13.5px]">
                    <thead>
                      <tr>
                        <th className={th}>Procedure</th>
                        <th className={th}>A #{d.a}</th>
                        <th className={th}>B #{d.b}</th>
                        <th className={cn(th, "text-right")}>
                          <HeaderHint label="Δ" tip="B minus A, in points. Colored only when flagged: a steady change is within the noise." />
                        </th>
                        <th className={th}>Flag</th>
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
                              <HrefLink href={sessionsHref(ws, { procedure: p.procedure_id, release: d.b ?? undefined }, scope)}>sessions on #{d.b}</HrefLink>
                            </div>
                          </td>
                          <td className={td}>
                            <Side r={p.a} thin={p.a.n < d.min_n} />
                          </td>
                          <td className={td}>
                            <Side r={p.b} thin={p.b.n < d.min_n} />
                          </td>
                          <td className={cn(td, "text-right")}>{p.delta_points != null ? <DeltaMark delta={{ value: p.delta_points, unit: "pts", good: p.flag === "steady" ? undefined : "up" }} /> : <span className="text-muted-foreground">—</span>}</td>
                          <td className={td}>
                            <FlagTag flag={p.flag} minN={d.min_n} points={d.flag_points} />
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
