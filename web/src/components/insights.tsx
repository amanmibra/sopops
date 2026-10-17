// Insights: overview (tiles, trend, scoreboard), a procedure (rule ladder, release split, warning impact, patterns by
// theme), a rule, and release comparison. Every number comes from the backend's one definition of eligible sessions
// and denominators; this file only shows them, always with n, and links each one to the sessions behind it.
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import type {
  CompareResponse,
  FiltersOut,
  InsightFilters,
  LadderRow,
  PatternOut,
  RateOut,
  RulePatternOut,
  SessionFilters,
  ThemeOut,
  Tiles as TilesT,
  Trend,
} from "@/lib/api";
import { WINDOWS, q } from "@/lib/api";
import { FAIL_STATUSES, type Scope, WINDOW_LABEL, fmtDelta, fmtPct, insightsHref, ruleName, sessionsHref } from "@/lib/insights";
import { when } from "@/lib/kinds";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { ErrorBanner, Note, PageTitle, Panel, Segmented, Sub, Tag } from "./bits";
import { HrefLink } from "./href-link";
import { Page } from "./item-shell";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";

const ANY = "any";

// --- filters ----------------------------------------------------------------------------------------------------

function Pick({ label, value, options, onChange }: { label: string; value: string | undefined; options: [string, string][]; onChange: (v: string | undefined) => void }) {
  return (
    <Select value={value ?? ANY} onValueChange={(v) => onChange(v === ANY ? undefined : v)}>
      <SelectTrigger size="sm" className="min-w-36" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ANY}>{`Any ${label.toLowerCase()}`}</SelectItem>
        {options.map(([v, l]) => (
          <SelectItem key={v} value={v}>
            {l}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** Window presets and the filters every Insights page shares; all of it lives in the URL. */
export function FilterBar({ filters, onChange, procedure = true, release = true }: { filters: InsightFilters; onChange: (f: InsightFilters) => void; procedure?: boolean; release?: boolean }) {
  const { cfg, releases } = useWs();
  const set = (patch: Partial<InsightFilters>) => onChange({ ...filters, ...patch });
  const agents = [...new Set([...cfg.agents.map((a) => a.id), ...(filters.agent ? [filters.agent] : [])])];
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
      <Segmented small label="Window" value={filters.window ?? "7d"} options={WINDOWS.map((w) => [w, w])} onChange={(window) => set({ window: window === "7d" ? undefined : window })} />
      {procedure && (
        <Pick label="Procedure" value={filters.procedure} options={cfg.procedures.map((p) => [p.id, p.name])} onChange={(procedure) => set({ procedure })} />
      )}
      <Pick label="Agent" value={filters.agent} options={agents.map((a) => [a, a])} onChange={(agent) => set({ agent })} />
      {release && (
        <Pick
          label="Release"
          value={filters.release?.[0] != null ? String(filters.release[0]) : undefined}
          options={[...releases].sort((a, b) => b.number - a.number).map((r) => [String(r.number), `#${r.number}${r.note ? ` ${r.note}` : ""}`])}
          onChange={(v) => set({ release: v ? [Number(v)] : undefined })}
        />
      )}
      <Pick label="Channel" value={filters.channel} options={[["voice", "Voice"], ["text", "Text"]]} onChange={(channel) => set({ channel: channel as InsightFilters["channel"] })} />
    </div>
  );
}

const scopeOf = (f: FiltersOut): Scope => ({ start: f.start, end: f.end, agent: f.agent, releases: f.releases, channel: f.channel });

function windowNote(f: FiltersOut) {
  return (
    <>
      Sessions that started {when(f.start)} – {when(f.end)}, compared with the same length of time before.
    </>
  );
}

// --- tiles ---------------------------------------------------------------------------------------------------------

function Tile({ label, value, sub, delta, href, help, tone }: { label: string; value: ReactNode; sub?: ReactNode; delta?: ReactNode; href?: string; help?: string; tone?: "ok" | "err" }) {
  const body = (
    <>
      <div className="text-muted-foreground text-[13px]">{label}</div>
      <div className={cn("text-[26px] leading-tight font-semibold tabular-nums", tone === "ok" && "text-ok", tone === "err" && "text-err")}>{value}</div>
      {sub && <div className="text-muted-foreground text-xs">{sub}</div>}
      {delta && <div className="text-muted-foreground mt-0.5 text-xs">{delta}</div>}
    </>
  );
  return (
    <div className="bg-card min-w-0 rounded-xl border px-3.5 py-3" title={help}>
      {href ? (
        <HrefLink href={href} className="text-foreground block hover:no-underline">
          {body}
        </HrefLink>
      ) : (
        body
      )}
    </div>
  );
}

export function TilesRow({ tiles, scope, procedure }: { tiles: TilesT; scope: Scope; procedure?: string }) {
  const { ws } = useWs();
  const c = tiles.current;
  const p = tiles.prior;
  const link = (f: SessionFilters) => sessionsHref(ws, procedure ? { procedure, ...f } : f, scope);
  const vs = (cur: ReactNode, unit: "pts" | "%", d: number | null | undefined) => (
    <>
      {fmtDelta(d, unit)} vs prior ({cur})
    </>
  );
  const outcome = (r: "followed" | "deviated"): SessionFilters => (procedure ? { procedure_result: r } : { result: r });
  return (
    <div className="mb-4 grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-6">
      <Tile
        label="Sessions checked"
        value={c.sessions_checked}
        sub={`+${c.excluded} excluded · ${c.in_progress} in progress · ${c.failed} failed`}
        delta={vs(p.sessions_checked, "%", tiles.delta_pct.sessions_checked)}
        href={link({ eligible: true })}
        help="Checked (or partial) sessions with a result. Excluded, in-progress and failed sessions are counted, never rated."
      />
      <Tile
        label="Followed end to end"
        value={fmtPct(c.followed_pct)}
        sub={`${c.followed} of ${c.sessions_checked}`}
        delta={vs(fmtPct(p.followed_pct), "pts", tiles.delta_points.followed_pct)}
        href={link(outcome("followed"))}
        tone="ok"
        help="Every procedure check in scope followed."
      />
      <Tile
        label="Need attention"
        value={fmtPct(c.need_attention_pct)}
        sub={`${c.need_attention} of ${c.sessions_checked}`}
        delta={vs(fmtPct(p.need_attention_pct), "pts", tiles.delta_points.need_attention_pct)}
        href={link(outcome("deviated"))}
        tone="err"
        help="Some procedure check in scope deviated: a step skipped or a never rule broken."
      />
      <Tile
        label="Excluded"
        value={c.excluded}
        sub="ended before a procedure started or finished"
        delta={vs(p.excluded, "%", tiles.delta_pct.excluded)}
        href={link({ status: "excluded" })}
      />
      <Tile
        label="Procedure pass rate"
        value={fmtPct(c.pass_rate)}
        sub={`n = ${c.measurable_checks} checks`}
        delta={vs(fmtPct(p.pass_rate), "pts", tiles.delta_points.pass_rate)}
        help="Procedure checks followed / measurable checks (not measurable ones are left out)."
      />
      <Tile label="Goal reached" value={fmtPct(c.goal_rate)} sub={`n = ${c.goal_n} checks`} delta={vs(fmtPct(p.goal_rate), "pts", tiles.delta_points.goal_rate)} />
    </div>
  );
}

// --- trend ---------------------------------------------------------------------------------------------------------

const SERIES = [
  { key: "followed", label: "Followed", color: "var(--ok)" },
  { key: "partial", label: "Partial", color: "var(--warn)" },
  { key: "deviated", label: "Deviated", color: "var(--err)" },
] as const;

/** Stacked outcomes per bucket, and below it (same x axis, its own y axis) the pass rate, with release markers. */
export function TrendChart({ trend }: { trend: Trend }) {
  const [hover, setHover] = useState<number | null>(null);
  const b = trend.buckets;
  if (!b.length || b.every((x) => x.n + x.not_measurable === 0)) return <Note>No checked sessions in this window.</Note>;
  const W = 800;
  const H1 = 150;
  const H2 = 70;
  const L = 34;
  const R = 8;
  const gap = 26;
  const plotW = W - L - R;
  const step = plotW / b.length;
  const barW = Math.max(2, Math.min(28, step - 2));
  const max = Math.max(1, ...b.map((x) => x.followed + x.partial + x.deviated));
  const y1 = (v: number) => H1 - (v / max) * (H1 - 8);
  const top2 = H1 + gap;
  const y2 = (v: number) => top2 + H2 - (v / 100) * H2;
  const xc = (i: number) => L + i * step + step / 2;
  const fmt = (iso: string) => new Date(iso).toLocaleDateString(undefined, trend.bucket === "day" ? { month: "short", day: "numeric" } : { month: "short", day: "numeric" });
  const bucketOf = (iso: string) => {
    const t = new Date(iso).getTime();
    let idx = -1;
    b.forEach((x, i) => {
      if (new Date(x.start).getTime() <= t) idx = i;
    });
    return idx;
  };
  const line = b
    .map((x, i) => (x.pass_rate == null ? null : `${xc(i)},${y2(x.pass_rate)}`))
    .reduce<string[][]>((acc, pt) => {
      if (pt == null) acc.push([]);
      else acc[acc.length - 1].push(pt);
      return acc;
    }, [[]])
    .filter((seg) => seg.length);
  const labelEvery = Math.ceil(b.length / 8);
  const h = hover != null ? b[hover] : null;
  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-center gap-3 text-[13px]" aria-hidden>
        {SERIES.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5">
            <span className="inline-block size-2.5 rounded-sm" style={{ background: s.color }} /> {s.label}
          </span>
        ))}
        <span className="flex items-center gap-1.5">
          <span className="bg-primary inline-block h-0.5 w-4" /> Pass rate (followed / measurable)
        </span>
        <span className="text-muted-foreground">per {trend.bucket}</span>
      </div>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${top2 + H2 + 22}`} className="w-full" role="img" aria-label={`Outcomes per ${trend.bucket}, and the pass rate`} onMouseLeave={() => setHover(null)}>
          {[0, max].map((v) => (
            <g key={`g${v}`}>
              <line x1={L} x2={W - R} y1={y1(v)} y2={y1(v)} stroke="var(--border)" strokeWidth={1} />
              <text x={L - 6} y={y1(v) + 4} textAnchor="end" fontSize={11} fill="var(--muted-foreground)">
                {v}
              </text>
            </g>
          ))}
          {[0, 50, 100].map((v) => (
            <g key={`p${v}`}>
              <line x1={L} x2={W - R} y1={y2(v)} y2={y2(v)} stroke="var(--border)" strokeWidth={1} strokeDasharray={v === 0 ? undefined : "2 3"} />
              <text x={L - 6} y={y2(v) + 4} textAnchor="end" fontSize={11} fill="var(--muted-foreground)">
                {v}%
              </text>
            </g>
          ))}
          {b.map((x, i) => {
            let acc = 0;
            return (
              <g key={x.start}>
                {SERIES.map((s) => {
                  const v = x[s.key];
                  if (!v) return null;
                  const y0 = y1(acc);
                  acc += v;
                  const yTop = y1(acc);
                  return <rect key={s.key} x={xc(i) - barW / 2} y={yTop + 1} width={barW} height={Math.max(0, y0 - yTop - 2)} rx={2} fill={s.color} opacity={hover == null || hover === i ? 1 : 0.45} />;
                })}
                {i % labelEvery === 0 && (
                  <text x={xc(i)} y={top2 + H2 + 16} textAnchor="middle" fontSize={11} fill="var(--muted-foreground)">
                    {fmt(x.start)}
                  </text>
                )}
                <rect x={L + i * step} y={0} width={step} height={top2 + H2} fill="transparent" onMouseEnter={() => setHover(i)} />
              </g>
            );
          })}
          {line.map((seg, k) => (
            <polyline key={k} points={seg.join(" ")} fill="none" stroke="var(--primary)" strokeWidth={2} strokeLinejoin="round" />
          ))}
          {b.map((x, i) => (x.pass_rate == null ? null : <circle key={x.start} cx={xc(i)} cy={y2(x.pass_rate)} r={hover === i ? 4.5 : 3} fill="var(--primary)" stroke="var(--card)" strokeWidth={2} />))}
          {trend.releases.map((m) => {
            const i = bucketOf(m.first_session);
            if (i < 0) return null;
            return (
              <g key={m.release}>
                <line x1={xc(i) - step / 2} x2={xc(i) - step / 2} y1={0} y2={top2 + H2} stroke="var(--k-procedure)" strokeWidth={1} strokeDasharray="3 3" />
                <text x={xc(i) - step / 2 + 3} y={10} fontSize={11} fill="var(--k-procedure)">
                  #{m.release}
                </text>
              </g>
            );
          })}
        </svg>
        {h && (
          <div className="bg-popover pointer-events-none absolute top-0 right-0 rounded-md border px-2.5 py-1.5 text-xs shadow-sm">
            <div className="font-semibold">{new Date(h.start).toLocaleDateString(undefined, { dateStyle: "medium" })}{trend.bucket === "week" ? " (week)" : ""}</div>
            <div>Followed {h.followed} · Partial {h.partial} · Deviated {h.deviated}</div>
            <div>
              Pass rate {fmtPct(h.pass_rate)} <span className="text-muted-foreground">(n = {h.n})</span>
            </div>
            {h.not_measurable > 0 && <div className="text-muted-foreground">{h.not_measurable} not measurable</div>}
          </div>
        )}
      </div>
      <p className="text-muted-foreground mt-1 text-xs">Dashed lines: the first session on each release.</p>
    </div>
  );
}

// --- overview ------------------------------------------------------------------------------------------------------

function Loading({ error, pending }: { error: unknown; pending: boolean }) {
  if (error) return <ErrorBanner error={error} />;
  if (pending) return <p className="text-muted-foreground">Loading…</p>;
  return null;
}

export function InsightsNav({ here, filters }: { here: "overview" | "releases" | "report"; filters: InsightFilters }) {
  const { ws } = useWs();
  const items: [typeof here, string, string[]][] = [
    ["overview", "Overview", ["insights"]],
    ["releases", "Release compare", ["insights", "releases"]],
    ["report", "Report", ["report"]],
  ];
  return (
    <div className="mb-3 flex flex-wrap gap-1 text-[13px]">
      {items.map(([k, label, path]) => (
        <HrefLink
          key={k}
          href={insightsHref(ws, path, filters)}
          className={cn("rounded-md px-2 py-0.5 text-foreground hover:bg-muted hover:no-underline", k === here && "bg-brand-soft font-semibold")}
        >
          {label}
        </HrefLink>
      ))}
    </div>
  );
}

const th = "text-muted-foreground border-b px-2.5 py-2 text-left text-[13px] font-semibold whitespace-nowrap";
const td = "border-b px-2.5 py-2 align-top";
const num = "border-b px-2.5 py-2 align-top text-right tabular-nums whitespace-nowrap";

function DeltaText({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="text-muted-foreground">—</span>;
  return <span className={cn(v > 0 && "text-ok", v < 0 && "text-err")}>{fmtDelta(v, "pts")}</span>;
}

export function OverviewView({ filters, onFilters }: { filters: InsightFilters; onFilters: (f: InsightFilters) => void }) {
  const { ws } = useWs();
  const res = useQuery(q.insightsOverview(ws, filters));
  const d = res.data;
  return (
    <Page wide>
      <InsightsNav here="overview" filters={filters} />
      <PageTitle>Insights</PageTitle>
      <Sub>How sessions went against your procedures, over the last {WINDOW_LABEL[filters.window ?? "7d"]}. Every number links to its sessions.</Sub>
      <FilterBar filters={filters} onChange={onFilters} />
      <Loading error={res.error} pending={res.isPending} />
      {d && (
        <>
          <TilesRow tiles={d.tiles} scope={scopeOf(d.filters)} procedure={d.filters.procedure ?? undefined} />
          <Panel title="Trend">
            <TrendChart trend={d.trend} />
          </Panel>
          <Panel title="Procedures">
            {d.scoreboard.length === 0 ? (
              <Note>No procedure checks in this window.</Note>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-[14px]">
                  <thead>
                    <tr>
                      <th className={th}>Procedure</th>
                      <th className={cn(th, "text-right")}>Checks</th>
                      <th className={cn(th, "text-right")}>Pass rate</th>
                      <th className={cn(th, "text-right")}>vs prior</th>
                      <th className={cn(th, "text-right")}>Deviated</th>
                      <th className={th}>Most failed rules</th>
                      <th className={cn(th, "text-right")}>Never broken</th>
                      <th className={cn(th, "text-right")}>Patterns</th>
                      <th className={cn(th, "text-right")}>Excluded</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.scoreboard.map((r) => {
                      const scope = scopeOf(d.filters);
                      return (
                        <tr key={r.procedure_id} className="hover:bg-muted">
                          <td className={td}>
                            <HrefLink href={insightsHref(ws, ["insights", "procedures", r.procedure_id], { ...filters, procedure: undefined })} className="font-semibold">
                              {r.procedure_name}
                            </HrefLink>
                          </td>
                          <td className={num}>
                            <HrefLink href={sessionsHref(ws, { procedure: r.procedure_id }, scope)} className="text-foreground">
                              {r.checks}
                            </HrefLink>
                          </td>
                          <td className={num}>
                            {fmtPct(r.pass_rate)} <span className="text-muted-foreground text-xs">n={r.measurable}</span>
                          </td>
                          <td className={num} title={`Prior: ${fmtPct(r.prior_pass_rate)} (n=${r.prior_measurable})`}>
                            <DeltaText v={r.delta_points} />
                          </td>
                          <td className={num}>
                            <HrefLink href={sessionsHref(ws, { procedure: r.procedure_id, procedure_result: "deviated" }, scope)} className="text-err">
                              {r.deviated}
                            </HrefLink>
                          </td>
                          <td className={cn(td, "text-[13px]")}>
                            {r.top_failing_rules.length ? (
                              r.top_failing_rules.map((x) => (
                                <div key={x.rule_id} className="max-w-[46ch] truncate" title={x.text ?? ""}>
                                  <HrefLink href={insightsHref(ws, ["insights", "procedures", r.procedure_id, "rules", x.rule_id], { ...filters, procedure: undefined })}>
                                    {ruleName(x.kind, x.index)}
                                  </HrefLink>{" "}
                                  <span className="text-muted-foreground">
                                    fails {fmtPct(x.fail_rate)} (n={x.n})
                                  </span>
                                </div>
                              ))
                            ) : (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </td>
                          <td className={num}>{r.never_violations}</td>
                          <td className={num}>{r.open_patterns}</td>
                          <td className={num}>{r.excluded}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
          <p className="text-muted-foreground text-xs">{windowNote(d.filters)}</p>
        </>
      )}
    </Page>
  );
}

// --- procedure -----------------------------------------------------------------------------------------------------

const FLAG: Record<string, { label: string; className: string; help: string }> = {
  thin: { label: "thin", className: "", help: "Fewer than 10 on a side: not flagged." },
  regressed: { label: "regressed", className: "bg-err-soft text-err border-transparent", help: "At least 5 points worse, and the 95% intervals don't overlap." },
  improved: { label: "improved", className: "bg-brand-soft text-primary border-transparent", help: "At least 5 points better, and the 95% intervals don't overlap." },
  steady: { label: "steady", className: "", help: "No clear change." },
};

export function FlagTag({ flag }: { flag: string }) {
  const f = FLAG[flag] ?? FLAG.steady;
  return (
    <span title={f.help}>
      <Tag className={f.className}>{f.label}</Tag>
    </span>
  );
}

function failFilter(kind: string, rule: string): SessionFilters {
  return { rule, rule_status: [...(FAIL_STATUSES[kind as keyof typeof FAIL_STATUSES] ?? [])] };
}

function Ladder({ rows, procedureId, scope, filters, split }: { rows: LadderRow[]; procedureId: string; scope: Scope; filters: InsightFilters; split: Map<string, { a: number | null; b: number | null; flag: string }> | null }) {
  const { ws } = useWs();
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[14px]">
        <thead>
          <tr>
            <th className={th}>Rule</th>
            <th className={cn(th, "text-right")}>n</th>
            <th className={cn(th, "text-right")}>Pass rate</th>
            <th className={cn(th, "text-right")} title="Steps: skipped / n">
              Skip rate
            </th>
            <th className={cn(th, "text-right")} title="Not reached or not applicable, of every judgment">
              Not counted
            </th>
            <th className={cn(th, "text-right")}>Findings</th>
            {split && <th className={th}>Release split</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const warn = r.kind === "warning";
            const s = split?.get(r.rule_id);
            return (
              <tr key={r.rule_id} className="hover:bg-muted">
                <td className={td}>
                  <HrefLink href={insightsHref(ws, ["insights", "procedures", procedureId, "rules", r.rule_id], filters)} className="font-semibold whitespace-nowrap">
                    {ruleName(r.kind, r.index)}
                  </HrefLink>
                  {r.wordings > 1 && <Tag className="ml-1.5">reworded</Tag>}
                  <div className="text-muted-foreground max-w-[60ch] text-[13px]">{r.text}</div>
                </td>
                <td className={num}>{r.n}</td>
                <td className={num}>
                  {warn ? (
                    <span title="Warning signs never count toward pass rates">
                      present {fmtPct(r.present_rate)}
                      {r.unheeded ? <div className="text-warn text-xs">{r.unheeded} unheeded</div> : null}
                    </span>
                  ) : (
                    fmtPct(r.pass_rate)
                  )}
                </td>
                <td className={num}>{r.kind === "step" ? fmtPct(r.skip_rate) : <span className="text-muted-foreground">—</span>}</td>
                <td className={num}>{fmtPct(r.not_counted_pct)}</td>
                <td className={num}>
                  {r.findings ? (
                    <HrefLink href={sessionsHref(ws, { procedure: procedureId, ...failFilter(r.kind, r.rule_id) }, scope)} className="text-err">
                      {r.findings}
                    </HrefLink>
                  ) : (
                    0
                  )}
                  {r.patterns > 0 && <div className="text-muted-foreground text-xs">{r.patterns} patterns</div>}
                </td>
                {split && (
                  <td className={cn(td, "text-[13px] whitespace-nowrap")}>
                    {s ? (
                      <>
                        {fmtPct(s.a)} → {fmtPct(s.b)} <FlagTag flag={s.flag} />
                      </>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PatternLine({ p, depth, scope }: { p: PatternOut; depth: number; scope?: Scope }) {
  const { ws } = useWs();
  return (
    <div className={cn("flex items-baseline gap-2 py-0.5", depth && "pl-5 text-[13px]")}>
      <HrefLink href={sessionsHref(ws, { pattern: p.pattern_id }, scope)} className={cn(depth === 0 && "font-semibold", p.kind === "other" && "text-muted-foreground")}>
        {p.name}
      </HrefLink>
      {p.status === "retired" && <Tag>retired</Tag>}
      <span className="text-muted-foreground text-xs tabular-nums">
        {p.sessions} sessions · {p.findings} findings
      </span>
      {depth > 0 && p.description && <span className="text-muted-foreground hidden truncate text-xs lg:inline">{p.description}</span>}
    </div>
  );
}

export function PatternsByTheme({ themes, scope }: { themes: ThemeOut[]; scope?: Scope }) {
  if (!themes.length) return <Note>No findings yet, so no patterns.</Note>;
  return (
    <div className="grid gap-3">
      {themes.map((t) => (
        <div key={t.theme_id ?? "none"}>
          <div className="mb-1 flex items-baseline gap-2">
            <h3 className="font-semibold">{t.name}</h3>
            <span className="text-muted-foreground text-xs">{t.sessions} sessions</span>
          </div>
          {t.description && <p className="text-muted-foreground mb-1 text-[13px]">{t.description}</p>}
          <div className="border-l pl-3">
            {t.patterns.map((p: RulePatternOut) => (
              <div key={p.pattern_id} className="mb-1">
                <PatternLine p={p} depth={0} scope={scope} />
                {p.rule?.text && (
                  <div className="text-muted-foreground mb-0.5 text-xs">
                    {ruleName(p.rule.kind, p.rule.index)}: {p.rule.text}
                  </div>
                )}
                {p.sub_patterns.map((s) => (
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

export function ProcedureInsightsView({ id, filters, onFilters }: { id: string; filters: InsightFilters; onFilters: (f: InsightFilters) => void }) {
  const { ws } = useWs();
  const res = useQuery(q.insightsProcedure(ws, id, filters));
  const d = res.data;
  const split = d?.release_split ? new Map(d.release_split.rules.map((r) => [r.rule_id, { a: r.a_pass_rate, b: r.b_pass_rate, flag: r.flag }])) : null;
  return (
    <Page wide>
      <div className="text-muted-foreground mb-2 text-[13px]">
        <HrefLink href={insightsHref(ws, ["insights"], filters)}>Insights</HrefLink> /
      </div>
      <PageTitle>{d?.procedure_name ?? id}</PageTitle>
      <Sub>
        Each rule's pass rate, where sessions fail, and the patterns behind it.{" "}
        <HrefLink href={wsHref(ws, "procedures", id)}>Edit the procedure</HrefLink>
      </Sub>
      <FilterBar filters={filters} onChange={onFilters} procedure={false} />
      <Loading error={res.error} pending={res.isPending} />
      {d && (
        <>
          <TilesRow tiles={d.tiles} scope={scopeOf(d.filters)} procedure={id} />
          <Panel title="Trend">
            <TrendChart trend={d.trend} />
          </Panel>
          <Panel
            title={
              <>
                Rules{" "}
                {d.release_split && (
                  <span className="text-muted-foreground text-[13px] font-normal">
                    · release split #{d.release_split.a} → #{d.release_split.b}, by the release each session ran (flags need n ≥ {d.release_split.min_n} on both sides)
                  </span>
                )}
              </>
            }
          >
            <Ladder rows={d.rules} procedureId={id} scope={scopeOf(d.filters)} filters={filters} split={split} />
            <p className="text-muted-foreground mt-2 text-xs">
              n = passes + fails; not reached and not applicable are left out. Warning signs show how often they came up, never a pass rate.
            </p>
          </Panel>
          {d.warning_impact.length > 0 && (
            <Panel title="Warning signs: does the procedure hold up when they come up?">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-[14px]">
                  <thead>
                    <tr>
                      <th className={th}>Warning sign</th>
                      <th className={cn(th, "text-right")}>Pass rate, present</th>
                      <th className={cn(th, "text-right")}>Pass rate, absent</th>
                      <th className={cn(th, "text-right")}>Δ</th>
                      <th className={cn(th, "text-right")}>Unheeded</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.warning_impact.map((w) => (
                      <tr key={w.rule_id}>
                        <td className={td}>
                          <span className="font-semibold">Warning sign {w.index}</span> {w.flag && <Tag className="bg-err-soft text-err border-transparent">drops 10+ pts</Tag>}
                          <div className="text-muted-foreground max-w-[60ch] text-[13px]">{w.text}</div>
                        </td>
                        <td className={num}>
                          <HrefLink href={sessionsHref(ws, { procedure: id, rule: w.rule_id, rule_status: ["present", "unheeded"] }, scopeOf(d.filters))} className="text-foreground">
                            {fmtPct(w.present_pass_rate)}
                          </HrefLink>{" "}
                          <span className="text-muted-foreground text-xs">n={w.present_n}</span>
                        </td>
                        <td className={num}>
                          {fmtPct(w.absent_pass_rate)} <span className="text-muted-foreground text-xs">n={w.absent_n}</span>
                        </td>
                        <td className={num}>
                          <DeltaText v={w.delta_points} />
                        </td>
                        <td className={num}>
                          {w.unheeded ? (
                            <HrefLink href={sessionsHref(ws, { procedure: id, rule: w.rule_id, rule_status: ["unheeded"] }, scopeOf(d.filters))} className="text-warn">
                              {w.unheeded}
                            </HrefLink>
                          ) : (
                            0
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-muted-foreground mt-2 text-xs">Over measurable checks that judged the sign; Δ only when both sides have n ≥ 10.</p>
            </Panel>
          )}
          <Panel title="Patterns by theme">
            <PatternsByTheme themes={d.themes} scope={scopeOf(d.filters)} />
          </Panel>
        </>
      )}
    </Page>
  );
}

// --- rule ----------------------------------------------------------------------------------------------------------

function Rate({ r }: { r: RateOut }) {
  return (
    <span className="tabular-nums">
      {fmtPct(r.pass_rate)} <span className="text-muted-foreground text-xs">n={r.n}</span>
      {r.interval && (
        <span className="text-muted-foreground text-xs">
          {" "}
          ({r.interval[0]}–{r.interval[1]}%)
        </span>
      )}
      {r.thin && r.n > 0 && <Tag className="ml-1">thin</Tag>}
    </span>
  );
}

export function RuleInsightsView({ procedureId, ruleId, filters, onFilters }: { procedureId: string; ruleId: string; filters: InsightFilters; onFilters: (f: InsightFilters) => void }) {
  const { ws, cfg } = useWs();
  const res = useQuery(q.insightsRule(ws, procedureId, ruleId, filters));
  const d = res.data;
  const procName = cfg.procedures.find((p) => p.id === procedureId)?.name ?? procedureId;
  const scope = d ? scopeOf(d.filters) : undefined;
  return (
    <Page wide>
      <div className="text-muted-foreground mb-2 text-[13px]">
        <HrefLink href={insightsHref(ws, ["insights"], filters)}>Insights</HrefLink> /{" "}
        <HrefLink href={insightsHref(ws, ["insights", "procedures", procedureId], filters)}>{procName}</HrefLink> /
      </div>
      <PageTitle>{d ? ruleName(d.kind, d.index) : "Rule"}</PageTitle>
      {d?.text && <Sub>{d.text}</Sub>}
      <FilterBar filters={filters} onChange={onFilters} procedure={false} />
      <Loading error={res.error} pending={res.isPending} />
      {d && scope && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-2.5 md:grid-cols-4">
            {d.kind === "warning" ? (
              <Tile label="Judged" value={d.overall.n} sub="warning signs have no pass rate" />
            ) : (
              <Tile
                label="Pass rate"
                value={fmtPct(d.overall.pass_rate)}
                sub={`n = ${d.overall.n}${d.overall.interval ? ` · 95%: ${d.overall.interval[0]}–${d.overall.interval[1]}%` : ""}`}
              />
            )}
            <Tile
              label="Fails"
              value={d.overall.fails ?? "—"}
              href={sessionsHref(ws, { procedure: procedureId, ...failFilter(d.kind, ruleId) }, scope)}
              tone={d.overall.fails ? "err" : undefined}
            />
            <Tile label="Not counted" value={d.overall.not_counted} sub="not reached or not applicable" />
            <Tile label="Patterns" value={d.patterns.reduce((n, p) => n + p.sub_patterns.filter((s) => s.kind === "sub").length, 0)} sub="named failure modes" />
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="By release">
              {d.releases.length ? (
                <table className="w-full border-collapse text-[14px]">
                  <tbody>
                    {d.releases.map((r) => (
                      <tr key={String(r.release)}>
                        <td className={td}>{r.release != null ? `#${r.release}` : "No release"}</td>
                        <td className={num}>
                          <Rate r={r} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <Note>No results in this window.</Note>
              )}
              <p className="text-muted-foreground mt-2 text-xs">By the release each session ran. Brackets: 95% interval. Thin: n &lt; 10.</p>
            </Panel>
            <Panel title="By agent">
              {d.agents.length ? (
                <table className="w-full border-collapse text-[14px]">
                  <tbody>
                    {d.agents.map((r) => (
                      <tr key={String(r.agent)}>
                        <td className={td}>{r.agent ?? "Unknown agent"}</td>
                        <td className={num}>
                          <Rate r={r} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <Note>No results in this window.</Note>
              )}
            </Panel>
          </div>
          <Panel title="Patterns">
            {d.patterns.length ? (
              d.patterns.map((p) => (
                <div key={p.pattern_id} className="mb-1">
                  <PatternLine p={p} depth={0} scope={scope} />
                  {p.sub_patterns.map((s) => (
                    <PatternLine key={s.pattern_id} p={s} depth={1} scope={scope} />
                  ))}
                </div>
              ))
            ) : (
              <Note>No findings on this rule.</Note>
            )}
          </Panel>
          <Panel title="Recent findings">
            {d.recent_findings.length ? (
              <ul className="grid gap-2">
                {d.recent_findings.map((f) => (
                  <li key={f.finding_id} className="text-[13px]">
                    <HrefLink href={wsHref(ws, "sessions", f.session_id)}>{when(f.started_at)}</HrefLink> <Tag className="bg-err-soft text-err border-transparent">{f.status.replace("_", " ")}</Tag>
                    {f.reason && <span className="text-muted-foreground"> {f.reason}</span>}
                    {f.evidence.slice(0, 2).map((e, i) => (
                      <div key={i} className="mt-0.5 ml-3">
                        “{e.quote}”
                      </div>
                    ))}
                  </li>
                ))}
              </ul>
            ) : (
              <Note>None in this window.</Note>
            )}
          </Panel>
          {d.wordings.length > 1 && (
            <Panel title="Wordings">
              <ul className="grid gap-1.5 text-[13px]">
                {d.wordings.map((w) => (
                  <li key={w.text_hash}>
                    “{w.text}” <span className="text-muted-foreground">({w.results} results, last {when(w.last_seen)})</span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </>
      )}
    </Page>
  );
}

// --- release compare -------------------------------------------------------------------------------------------------

function CompareRateCell({ r }: { r: { n: number; pass_rate: number | null; interval: [number, number] | null } }) {
  return (
    <span className="tabular-nums">
      {fmtPct(r.pass_rate)} <span className="text-muted-foreground text-xs">n={r.n}</span>
      {r.interval && (
        <div className="text-muted-foreground text-xs">
          {r.interval[0]}–{r.interval[1]}%
        </div>
      )}
    </span>
  );
}

export function CompareView({ filters, onFilters, a, b, onPick }: { filters: InsightFilters; onFilters: (f: InsightFilters) => void; a?: number; b?: number; onPick: (a?: number, b?: number) => void }) {
  const { ws } = useWs();
  const res = useQuery(q.insightsCompare(ws, filters, a, b));
  const d: CompareResponse | undefined = res.data;
  const opts = (d?.releases ?? []).map((r) => [String(r), `#${r}`] as [string, string]);
  return (
    <Page wide>
      <InsightsNav here="releases" filters={filters} />
      <PageTitle>Release compare</PageTitle>
      <Sub>
        Pass rates of release B against release A, per procedure and per rule, by the release each session ran. A change is flagged only when both sides have at least{" "}
        {d?.min_n ?? 10} results, it's {d?.flag_points ?? 5} points or more, and the 95% intervals don't overlap.
      </Sub>
      <FilterBar filters={filters} onChange={onFilters} release={false} />
      <div className="mb-4 flex flex-wrap items-center gap-2 text-[13px]">
        A
        <Pick label="Baseline" value={d?.a != null ? String(d.a) : undefined} options={opts} onChange={(v) => onPick(v ? Number(v) : undefined, b)} />
        B
        <Pick label="Candidate" value={d?.b != null ? String(d.b) : undefined} options={opts} onChange={(v) => onPick(a, v ? Number(v) : undefined)} />
      </div>
      <Loading error={res.error} pending={res.isPending} />
      {d &&
        (d.a == null || d.b == null ? (
          <Note>Comparing needs sessions on two releases in this window.</Note>
        ) : (
          <>
            <Panel title={`Procedures: #${d.a} → #${d.b}`}>
              <table className="w-full border-collapse text-[14px]">
                <thead>
                  <tr>
                    <th className={th}>Procedure</th>
                    <th className={cn(th, "text-right")}>A #{d.a}</th>
                    <th className={cn(th, "text-right")}>B #{d.b}</th>
                    <th className={cn(th, "text-right")}>Δ</th>
                    <th className={th}>Flag</th>
                  </tr>
                </thead>
                <tbody>
                  {d.procedures.map((p) => (
                    <tr key={p.procedure_id}>
                      <td className={td}>
                        <HrefLink href={insightsHref(ws, ["insights", "procedures", p.procedure_id], filters)}>{p.procedure_name}</HrefLink>
                      </td>
                      <td className={num}>
                        <CompareRateCell r={p.a} />
                      </td>
                      <td className={num}>
                        <CompareRateCell r={p.b} />
                      </td>
                      <td className={num}>
                        <DeltaText v={p.delta_points} />
                      </td>
                      <td className={td}>
                        <FlagTag flag={p.flag} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
            <Panel title="Rules">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-[14px]">
                  <thead>
                    <tr>
                      <th className={th}>Rule</th>
                      <th className={cn(th, "text-right")}>A #{d.a}</th>
                      <th className={cn(th, "text-right")}>B #{d.b}</th>
                      <th className={cn(th, "text-right")}>Δ</th>
                      <th className={th}>Flag</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.rules.map((r) => (
                      <tr key={`${r.procedure_id}/${r.rule_id}`} className="hover:bg-muted">
                        <td className={td}>
                          <HrefLink href={insightsHref(ws, ["insights", "procedures", r.procedure_id, "rules", r.rule_id], filters)} className="font-semibold">
                            {ruleName(r.kind, r.index)}
                          </HrefLink>
                          {r.rule_change && <Tag className="ml-1.5" tone={r.rule_change === "removed" ? "removed" : "changed"}>{r.rule_change === "changed" ? "rule changed" : r.rule_change}</Tag>}
                          <div className="text-muted-foreground max-w-[60ch] text-[13px]">{r.text}</div>
                        </td>
                        <td className={num}>
                          <CompareRateCell r={r.a} />
                        </td>
                        <td className={num}>
                          <CompareRateCell r={r.b} />
                        </td>
                        <td className={num} title={r.delta_points == null && r.raw_delta_points != null ? `${fmtDelta(r.raw_delta_points, "pts")} on too few results` : undefined}>
                          <DeltaText v={r.delta_points} />
                        </td>
                        <td className={td}>
                          <FlagTag flag={r.flag} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-muted-foreground mt-2 text-xs">Warning signs aren't compared: they have no pass rate. A reworded rule is still compared by its id; check the wording before reading its change.</p>
            </Panel>
          </>
        ))}
    </Page>
  );
}
