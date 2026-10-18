// Insights charts, all on recharts with the theme's CSS variables (so light and dark both work): the outcome trend
// (stacked outcomes, and under it the pass rate on its own axis), what went wrong, pass rate per release, and a
// rate per group that folds anything past the palette into "Other" instead of dropping it.
import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ErrorBar, LabelList, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Problem, ReleasePoint, Trend } from "@/lib/api";
import { OUTCOMES, fmtPct } from "@/lib/insights";
import { useGo } from "../href-link";
import { AXIS, ChartTip, Legend } from "./kit";

const label = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });

/** Which bucket a release's first session falls in, by label, for its marker. */
function markers(trend: Trend) {
  const rows = trend.buckets;
  return trend.releases
    .map((m) => {
      const t = Date.parse(m.first_session);
      const b = [...rows].reverse().find((r) => Date.parse(r.start) <= t);
      return b ? { release: m.release, x: label(b.start) } : null;
    })
    .filter((m): m is { release: number; x: string } => m != null);
}

/** Outcomes per bucket (stacked), and under it the pass rate with its own 0–100% axis: two charts on one x axis,
 * never one chart with two scales. Selecting a bucket opens its sessions. */
export function TrendCharts({ trend, bucketHref }: { trend: Trend; bucketHref: (b: { start: string; end: string }) => string }) {
  const go = useGo();
  const rows = useMemo(() => trend.buckets.map((b) => ({ ...b, x: label(b.start) })), [trend]);
  const marks = useMemo(() => markers(trend), [trend]);
  const open = (x: unknown) => {
    const row = rows.find((r) => r.x === x);
    if (row) go(bucketHref(row));
  };
  const unit = trend.bucket;
  const sync = "insights-trend";
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <Legend items={[...OUTCOMES.map((o) => ({ label: o.label, color: o.color })), { label: "Pass rate", color: "var(--primary)", line: true }, { label: "Release", color: "var(--k-procedure)", line: true }]} />
        <span className="text-muted-foreground text-xs">Per {unit}. Select a {unit} to open its sessions.</span>
      </div>
      <div className="h-44">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} syncId={sync} margin={{ top: 14, right: 8, bottom: 0, left: 0 }} onClick={(s) => open(s?.activeLabel)} className="cursor-pointer">
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="x" hide />
            <YAxis {...AXIS} allowDecimals={false} width={44} />
            <Tooltip
              cursor={{ fill: "var(--muted)" }}
              content={(p) => (
                <ChartTip
                  {...p}
                  head={(x) => {
                    const r = rows.find((row) => row.x === x);
                    return r ? `${unit === "week" ? "Week of " : ""}${x}: ${r.followed + r.partial + r.deviated} measurable${r.not_measurable ? `, ${r.not_measurable} not measurable` : ""}` : x;
                  }}
                  footer={(x) => {
                    const r = rows.find((row) => row.x === x);
                    return r ? `Pass rate ${fmtPct(r.pass_rate)} (n=${r.n}). One session counts once, by its worst procedure result.` : null;
                  }}
                />
              )}
            />
            {OUTCOMES.map((o, i) => (
              <Bar key={o.key} dataKey={o.key} name={o.label} stackId="o" fill={o.color} maxBarSize={36} radius={i === OUTCOMES.length - 1 ? [3, 3, 0, 0] : 0} stroke="var(--card)" strokeWidth={1} isAnimationActive={false} />
            ))}
            {marks.map((m) => (
              <ReferenceLine key={m.release} x={m.x} stroke="var(--k-procedure)" strokeDasharray="4 3" label={{ value: `#${m.release}`, position: "top", fontSize: 11, fill: "var(--k-procedure)" }} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="h-28">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} syncId={sync} margin={{ top: 6, right: 8, bottom: 0, left: 0 }} onClick={(s) => open(s?.activeLabel)} className="cursor-pointer">
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="x" {...AXIS} interval="preserveStartEnd" minTickGap={24} />
            <YAxis {...AXIS} domain={[0, 100]} ticks={[0, 50, 100]} unit="%" width={44} />
            <Tooltip content={(p) => <ChartTip {...p} format={(v, e) => `${fmtPct(v as number | null)} (n=${(e.payload as { n: number }).n})`} />} />
            {marks.map((m) => (
              <ReferenceLine key={m.release} x={m.x} stroke="var(--k-procedure)" strokeDasharray="4 3" />
            ))}
            <Line type="monotone" dataKey="pass_rate" name="Pass rate" stroke="var(--primary)" strokeWidth={2} dot={{ r: 3, strokeWidth: 2, fill: "var(--card)" }} activeDot={{ r: 5 }} connectNulls={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** What went wrong: measurable checks with each problem (they overlap). Selecting a bar opens those sessions. */
export function ProblemsChart({ problems, problemHref }: { problems: Problem[]; problemHref: (p: Problem) => string }) {
  const go = useGo();
  const color = (p: Problem) => (p.type === "skipped" || p.type === "never" ? "var(--o-deviated)" : "var(--o-partial)");
  return (
    <div className="h-52">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={problems.map((p) => ({ ...p, lbl: `${p.checks} · ${fmtPct(p.pct)}` }))} layout="vertical" margin={{ top: 0, right: 64, bottom: 0, left: 0 }}>
          <XAxis type="number" hide domain={[0, "dataMax"]} />
          <YAxis type="category" dataKey="label" width={168} {...AXIS} tick={{ fontSize: 12, fill: "var(--foreground)" }} />
          <Tooltip
            cursor={{ fill: "var(--muted)" }}
            content={(p) => (
              <ChartTip
                {...p}
                head={(x) => {
                  const r = problems.find((row) => row.label === x);
                  return r ? `${x}: ${fmtPct(r.pct)} of ${r.of} checks` : x;
                }}
                footer={() => "One check can have several problems. Select to open those sessions."}
              />
            )}
          />
          <Bar dataKey="checks" name="Checks" barSize={14} radius={[0, 3, 3, 0]} isAnimationActive={false} className="cursor-pointer" onClick={(d) => go(problemHref(d.payload as Problem))}>
            {problems.map((p) => (
              <Cell key={p.type} fill={color(p)} />
            ))}
            <LabelList dataKey="lbl" position="right" fontSize={12} fill="var(--foreground)" />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Pass rate per release with its 95% interval and n; thin releases are drawn faded. */
export function ReleaseTimeline({ points, highlight, onPick, sessions = true }: { points: ReleasePoint[]; highlight: (number | null)[]; onPick?: (release: number) => void; sessions?: boolean }) {
  const rows = points.map((p) => ({ ...p, x: `#${p.release}`, err: p.interval && p.pass_rate != null ? [p.pass_rate - p.interval[0], p.interval[1] - p.pass_rate] : [0, 0] }));
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 18, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="x" {...AXIS} />
          <YAxis {...AXIS} domain={[0, 100]} unit="%" width={44} />
          <Tooltip
            cursor={{ fill: "var(--muted)" }}
            content={(p) => (
              <ChartTip
                {...p}
                format={(v, e) => {
                  const r = e.payload as (typeof rows)[number];
                  return `${fmtPct(v as number | null)} (${r.passes} of ${r.n}${r.interval ? `, 95%: ${r.interval[0]}–${r.interval[1]}%` : ""})`;
                }}
                footer={(x) => {
                  const r = rows.find((row) => row.x === x);
                  return r ? `${sessions ? `${r.sessions} sessions ran this release. ` : ""}${r.thin ? "Thin: too few to compare." : "By the release each session ran."}` : null;
                }}
              />
            )}
          />
          <Bar dataKey="pass_rate" name="Pass rate" maxBarSize={44} radius={[3, 3, 0, 0]} isAnimationActive={false} onClick={(d) => onPick?.((d.payload as ReleasePoint).release)} className={onPick ? "cursor-pointer" : undefined}>
            {rows.map((r) => (
              <Cell key={r.release} fill={highlight.includes(r.release) ? "var(--primary)" : "var(--o-none)"} fillOpacity={r.thin ? 0.45 : 1} />
            ))}
            <ErrorBar dataKey="err" direction="y" width={8} stroke="var(--foreground)" strokeWidth={1.5} />
            <LabelList dataKey="n" position="top" fontSize={11} fill="var(--muted-foreground)" formatter={(v) => `n=${v}`} offset={10} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

const GROUP_MAX = 8;

/** A pass rate per group (agent, release…) as horizontal bars. Past GROUP_MAX groups the smallest fold into
 * "Other", pooled, so no group is ever dropped from the picture. */
export function RateByGroup({ rows }: { rows: { name: string; n: number; passes: number; thin: boolean }[] }) {
  const sorted = [...rows].sort((a, b) => b.n - a.n);
  const kept = sorted.slice(0, sorted.length > GROUP_MAX ? GROUP_MAX - 1 : GROUP_MAX);
  const rest = sorted.slice(kept.length);
  const all = rest.length
    ? [...kept, { name: `Other (${rest.length})`, n: rest.reduce((a, r) => a + r.n, 0), passes: rest.reduce((a, r) => a + r.passes, 0), thin: false }]
    : kept;
  const data = all.map((r) => {
    const rate = r.n ? Math.round((100 * r.passes) / r.n) : null;
    return { ...r, rate, lbl: `${rate == null ? "—" : `${rate}%`} · n=${r.n}` };
  });
  return (
    <div style={{ height: Math.max(80, data.length * 30 + 10) }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 84, bottom: 0, left: 0 }}>
          <XAxis type="number" hide domain={[0, 100]} />
          <YAxis type="category" dataKey="name" width={150} {...AXIS} tick={{ fontSize: 12, fill: "var(--foreground)" }} />
          <Tooltip cursor={{ fill: "var(--muted)" }} content={(p) => <ChartTip {...p} format={(v, e) => `${fmtPct(v as number | null)} (${(e.payload as { passes: number }).passes} of ${(e.payload as { n: number }).n})`} />} />
          <Bar dataKey="rate" name="Pass rate" barSize={14} radius={[0, 3, 3, 0]} isAnimationActive={false}>
            {data.map((r) => (
              <Cell key={r.name} fill="var(--primary)" fillOpacity={r.thin ? 0.4 : 1} />
            ))}
            <LabelList dataKey="lbl" position="right" fontSize={12} fill="var(--foreground)" />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
