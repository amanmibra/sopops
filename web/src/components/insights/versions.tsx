// A/B tests in two steps. Pick: every version in the time range, one compact row each (a version is the sessions with
// the same agent, release and config, named by a hash of them); tick up to five and press Compare. Compare: the picked
// versions lettered A to E in the order picked (A the baseline), what differs between them, a chart of each
// procedure's pass rate with its 95% range, and the procedure and rule tables, each version against the baseline with
// the release compare's flags. With nothing to compare yet, a short setup guide and the docs link.
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, BookOpen, Check, FlaskConical } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import type { InsightFilters, VersionOut, VersionProcedure, VersionProcedureCell, VersionRule, VersionRuleCell, VersionsResponse } from "@/lib/api";
import { q } from "@/lib/api";
import { fmtPct, insightsHref, ruleName } from "@/lib/insights";
import { when } from "@/lib/kinds";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { RuleText, Segmented } from "../bits";
import { HrefLink } from "../href-link";
import { Button } from "../ui/button";
import { Hint, OutcomeBar, OutcomeLegend, RateN, Skel, StatusBar, WidgetCard } from "./kit";
import { LIVE } from "./overview";
import { FlagTag } from "./procedure";
import { AsOf, InsightsShell, Pick } from "./scope";

export const AB_DOCS = "https://github.com/amanmibra/sopops/blob/main/docs/ab-tests.md";

const th = "text-muted-foreground border-b px-3 py-2 text-left text-xs font-medium whitespace-nowrap";
const td = "border-b px-3 py-2.5 align-top";

const SETTING: Record<string, string> = { agent: "Agent", release: "Release", llm: "LLM", stt: "Speech to text", tts: "Text to speech", voice: "Voice", prompt: "Prompt", version: "Your label", temperature: "Temperature" };
const settingName = (k: string) => SETTING[k] ?? k.replace(/_/g, " ");

function settingValue(v: VersionOut, k: string): string | null {
  if (k === "agent") return v.agent;
  if (k === "release") return v.release != null ? `#${v.release}` : null;
  const x = v.config[k];
  return x == null ? null : String(x);
}

const LETTERS = "ABCDE";
const COLORS = ["var(--side-a)", "var(--side-b)", "var(--side-c)", "var(--side-d)", "var(--side-e)"];
const shortId = (v: VersionOut) => v.id.slice(0, 8);
/** A picked version's place: its letter and color, A (the baseline) first. */
const slot = (d: VersionsResponse, id: string) => {
  const i = d.selected.indexOf(id);
  return i < 0 ? null : { letter: LETTERS[i] ?? "?", color: COLORS[i] ?? COLORS[1] };
};

/** A picked version's letter in its color; hover for its id and model. */
function VersionBadge({ v, d, className }: { v: VersionOut; d: VersionsResponse; className?: string }) {
  const s = slot(d, v.id);
  if (!s) return null;
  return (
    <Hint
      focusable={false}
      tip={
        <span className="grid gap-0.5">
          <span>
            Version <span className="font-mono">{v.id}</span>
            {v.id === d.baseline && " (baseline)"}
          </span>
          {v.config.llm != null && <span className="text-muted-foreground">{String(v.config.llm)}</span>}
        </span>
      }
    >
      <span
        className={cn("dark:text-background inline-flex h-5 min-w-5 shrink-0 cursor-help items-center justify-center rounded px-1 text-xs font-bold text-white", className)}
        style={{ background: s.color }}
      >
        {s.letter}
      </span>
    </Hint>
  );
}

const ORDER = ["llm", "temperature", "stt", "tts", "voice"];
const rank = (k: string) => (ORDER.includes(k) ? ORDER.indexOf(k) : ORDER.length);

/** The settings a version reported, on one line: "llm openai/gpt-4.1-mini · voice ava". */
function SettingsLine({ v }: { v: VersionOut }) {
  const keys = Object.keys(v.config).filter((k) => k !== "prompt").sort((a, b) => rank(a) - rank(b));
  if (!keys.length && !v.config.prompt) return <span className="text-muted-foreground text-xs">No settings reported</span>;
  return (
    <span className="flex flex-wrap gap-x-3 gap-y-0.5 text-[13px]">
      {keys.map((k) => (
        <span key={k} className="whitespace-nowrap">
          <span className="text-muted-foreground">{settingName(k)}</span> <span className="font-mono text-[12px]">{String(v.config[k])}</span>
        </span>
      ))}
      {v.prompt && (
        <Hint focusable={false} tip={<span className="block max-h-60 overflow-auto whitespace-pre-wrap">{v.prompt}</span>}>
          <span className="text-muted-foreground cursor-help border-b border-dotted">prompt</span>
        </Hint>
      )}
    </span>
  );
}

/** Step 1: tick the versions to compare. */
function PickStep({ d, initial, onCompare }: { d: VersionsResponse; initial: string[]; onCompare: (v: string[]) => void }) {
  const withSettings = d.versions.filter((v) => v.config_hash);
  const without = d.versions.filter((v) => !v.config_hash);
  const [picked, setPicked] = useState<string[]>(initial);
  useEffect(() => setPicked(initial), [initial.join()]);
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length >= d.max_versions ? p : [...p, id]));
  return (
    <section className="bg-card overflow-hidden rounded-lg border">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
        <div className="text-[13px]">
          <b className="font-semibold">Pick up to {d.max_versions} versions.</b>{" "}
          <span className="text-muted-foreground">The first you pick is A, the baseline the others are measured against.</span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-[13px]">
          <thead>
            <tr>
              <th className={cn(th, "w-14")} />
              <th className={th}>Version</th>
              <th className={th}>Agent</th>
              <th className={th}>Settings</th>
              <th className={cn(th, "text-right")}>Calls</th>
              <th className={th}>Last call</th>
            </tr>
          </thead>
          <tbody>
            {[...withSettings, ...without].map((v, n) => {
              const i = picked.indexOf(v.id);
              const on = i >= 0;
              const full = !on && picked.length >= d.max_versions;
              return (
                <Fragment key={v.id}>
                {n === withSettings.length && withSettings.length > 0 && (
                  <tr>
                    <td colSpan={6} className="text-muted-foreground bg-muted/30 border-b px-3 py-1.5 text-xs">
                      No settings reported: these versions differ only by agent or release.
                    </td>
                  </tr>
                )}
                <tr
                  className={cn("cursor-pointer", on ? "bg-muted/50" : "hover:bg-muted/40", full && "cursor-not-allowed opacity-50")}
                  onClick={() => !full && toggle(v.id)}
                >
                  <td className="border-b px-3 py-1.5">
                    <span className="flex items-center gap-1.5">
                      <input
                        id={`pick-${v.id}`}
                        type="checkbox"
                        className="size-4"
                        checked={on}
                        disabled={full}
                        onChange={() => toggle(v.id)}
                        onClick={(e) => e.stopPropagation()}
                        aria-label={`Compare version ${shortId(v)}`}
                      />
                      {on && (
                        <span className="dark:text-background inline-flex h-4 min-w-4 items-center justify-center rounded px-0.5 text-[11px] font-bold text-white" style={{ background: COLORS[i] }}>
                          {LETTERS[i]}
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="border-b px-3 py-1.5 font-mono text-[13px]" title={v.id}>
                    {shortId(v)}
                  </td>
                  <td className="border-b px-3 py-1.5 whitespace-nowrap">
                    {v.agent ?? "no agent"}
                    {v.release != null && <span className="text-muted-foreground ml-1.5 text-xs">release #{v.release}</span>}
                  </td>
                  <td className="border-b px-3 py-1.5">
                    <SettingsLine v={v} />
                  </td>
                  <td className="border-b px-3 py-1.5 text-right whitespace-nowrap tabular-nums">
                    {v.sessions}
                    {v.checked !== v.sessions && <span className="text-muted-foreground"> ({v.checked} checked)</span>}
                  </td>
                  <td className="text-muted-foreground border-b px-3 py-1.5 text-xs whitespace-nowrap">{when(v.last_at)}</td>
                </tr>
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="bg-card/95 sticky bottom-0 flex flex-wrap items-center justify-between gap-2 border-t px-4 py-2.5 backdrop-blur">
        <span className="text-muted-foreground text-xs">
          {picked.length < 2 ? `Pick ${2 - picked.length} more to compare.` : `${picked.length} versions picked.`}
          {picked.length > 0 && (
            <button type="button" className="hover:text-foreground ml-2 underline underline-offset-2" onClick={() => setPicked([])}>
              Clear
            </button>
          )}
        </span>
        <Button size="sm" disabled={picked.length < 2} onClick={() => onCompare(picked)}>
          Compare {picked.length >= 2 ? picked.length : ""} versions
        </Button>
      </div>
    </section>
  );
}

/** Step 2's header: the picked versions, side by side, with what differs between them. */
function Lineup({ d, onPick }: { d: VersionsResponse; onPick: (v: string[]) => void }) {
  const by = new Map(d.versions.map((v) => [v.id, v]));
  const picked = d.selected.map((id) => by.get(id)).filter((v): v is VersionOut => !!v);
  const keys = d.differs.length ? d.differs : ["llm"];
  const same = Array.from(new Set(picked.flatMap((v) => Object.keys(v.config)))).filter((k) => !keys.includes(k) && k !== "prompt");
  return (
    <section className="bg-card overflow-x-auto rounded-lg border">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr>
            <th className={cn(th, "w-40")} />
            {picked.map((v) => (
              <th key={v.id} className={cn(th, "text-foreground")}>
                <span className="flex items-center gap-2">
                  <VersionBadge v={v} d={d} />
                  <span className="font-mono text-[12px] font-normal">{shortId(v)}</span>
                  {v.id === d.baseline ? (
                    <span className="text-muted-foreground font-normal">baseline</span>
                  ) : (
                    <button
                      type="button"
                      className="text-muted-foreground hover:text-foreground font-normal underline underline-offset-2"
                      onClick={() => onPick([v.id, ...d.selected.filter((x) => x !== v.id)])}
                    >
                      Make baseline
                    </button>
                  )}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k}>
              <td className="text-muted-foreground border-b px-3 py-1.5 font-medium">{settingName(k)}</td>
              {picked.map((v) => (
                <td key={v.id} className="border-b px-3 py-1.5 font-mono text-[12px] font-semibold">
                  {settingValue(v, k) ?? <span className="text-muted-foreground font-sans font-normal">not reported</span>}
                </td>
              ))}
            </tr>
          ))}
          <tr>
            <td className="text-muted-foreground px-3 py-1.5">Calls</td>
            {picked.map((v) => (
              <td key={v.id} className="px-3 py-1.5 tabular-nums">
                {v.checked} checked <span className="text-muted-foreground text-xs">of {v.sessions}</span>
              </td>
            ))}
          </tr>
        </tbody>
      </table>
      {same.length > 0 && (
        <div className="text-muted-foreground border-t px-3 py-1.5 text-xs">
          Same on all: {same.map((k) => `${settingName(k)} ${String(picked[0]?.config[k] ?? "")}`).join(" · ")}
        </div>
      )}
    </section>
  );
}

/** Each procedure's pass rate per version, as a bar with its 95% range, on one 0 to 100 scale. */
function Chart({ d }: { d: VersionsResponse }) {
  const by = new Map(d.versions.map((v) => [v.id, v]));
  const t = d.thresholds;
  return (
    <WidgetCard title="Pass rate by procedure" subtitle="Bars are pass rates; the thin line is the 95% range. Ranges that overlap mean the difference could be chance." flush>
      <div className="grid gap-4 px-4 py-3">
        {d.procedures.map((p: VersionProcedure) => (
          <div key={p.procedure_id} className="grid gap-1.5">
            <div className="text-[13px] font-semibold">{p.procedure_name}</div>
            {p.cells.map((c: VersionProcedureCell) => {
              const v = by.get(c.version);
              const s = slot(d, c.version);
              if (!v || !s) return null;
              const [lo, hi] = c.interval ?? [0, 0];
              return (
                <div key={c.version} className="grid grid-cols-[1.5rem_1fr_13rem] items-center gap-2">
                  <VersionBadge v={v} d={d} />
                  <div className="bg-muted relative h-4 rounded-sm">
                    {c.pass_rate != null && (
                      <>
                        <div className="absolute inset-y-0 left-0 rounded-sm opacity-85" style={{ width: `${c.pass_rate}%`, background: s.color }} />
                        {c.interval && <div className="bg-foreground/70 absolute top-1/2 h-px -translate-y-1/2" style={{ left: `${lo}%`, width: `${Math.max(hi - lo, 0.5)}%` }} />}
                        {c.interval && <div className="bg-foreground/70 absolute top-0.5 bottom-0.5 w-px" style={{ left: `${lo}%` }} />}
                        {c.interval && <div className="bg-foreground/70 absolute top-0.5 bottom-0.5 w-px" style={{ left: `${hi}%` }} />}
                      </>
                    )}
                  </div>
                  <span className="flex items-center gap-1.5 text-[13px] whitespace-nowrap tabular-nums">
                    {c.pass_rate == null ? <span className="text-muted-foreground">no results</span> : <b>{fmtPct(c.pass_rate)}</b>}
                    <span className="text-muted-foreground text-xs">of {c.n}</span>
                    {c.flag && c.flag !== "thin" && <FlagTag flag={c.flag} minN={t.min_n} points={t.flag_points} />}
                  </span>
                </div>
              );
            })}
          </div>
        ))}
        <div className="text-muted-foreground grid grid-cols-[1.5rem_1fr_13rem] gap-2 text-xs tabular-nums">
          <span />
          <span className="flex justify-between">
            <span>0%</span>
            <span>50%</span>
            <span>100%</span>
          </span>
          <span />
        </div>
      </div>
    </WidgetCard>
  );
}

function Columns({ d }: { d: VersionsResponse }) {
  const by = new Map(d.versions.map((v) => [v.id, v]));
  return (
    <>
      {d.selected.map((id) => {
        const v = by.get(id);
        return (
          <th key={id} className={th}>
            {v && <VersionBadge v={v} d={d} />}
          </th>
        );
      })}
    </>
  );
}

const STEPS = ["Pick versions", "Compare"] as const;

function Steps({ step, onStep }: { step: 0 | 1; onStep: (s: 0 | 1) => void }) {
  return (
    <ol className="flex items-center gap-2 text-[13px]">
      {STEPS.map((label, i) => (
        <li key={label} className="flex items-center gap-2">
          {i > 0 && <span className="bg-border h-px w-6" />}
          <button
            type="button"
            disabled={i === 1 && step === 0}
            onClick={() => onStep(i as 0 | 1)}
            className={cn("flex items-center gap-1.5 rounded-full border px-2.5 py-0.5", i === step ? "border-foreground font-medium" : "text-muted-foreground", i === 1 && step === 0 && "cursor-default")}
          >
            <span className={cn("inline-flex size-4 items-center justify-center rounded-full text-[11px]", i === step ? "bg-foreground text-background" : "bg-muted")}>
              {i < step ? <Check className="size-3" /> : i + 1}
            </span>
            {label}
          </button>
        </li>
      ))}
    </ol>
  );
}

function ProcCell({ c, minN, points }: { c: VersionProcedureCell; minN: number; points: number }) {
  return (
    <span className="grid gap-1">
      <span className="flex items-center gap-2">
        <RateN rate={c.pass_rate} n={c.n} thin={c.thin} />
        {c.flag && c.flag !== "thin" && <FlagTag flag={c.flag} minN={minN} points={points} />}
      </span>
      <OutcomeBar followed={c.followed} partial={c.partial} deviated={c.deviated} className="w-24" />
      <span className="text-muted-foreground text-xs tabular-nums">
        Goal met {fmtPct(c.goal_rate)} of {c.goal_n} · applied to {fmtPct(c.trigger_rate)}
      </span>
    </span>
  );
}

function RuleCell({ c, kind, minN, points }: { c: VersionRuleCell; kind: string | null | undefined; minN: number; points: number }) {
  if (!c.n) return <span className="text-muted-foreground text-xs">no results</span>;
  if (kind === "warning")
    return (
      <span className="tabular-nums">
        <b className="font-semibold">{fmtPct(c.present_rate)}</b> <span className="text-muted-foreground text-xs">present, of {c.n}</span>
      </span>
    );
  return (
    <span className="grid gap-1">
      <span className="flex items-center gap-2">
        <RateN rate={c.pass_rate} n={c.n} thin={c.thin} />
        {c.flag && c.flag !== "thin" && <FlagTag flag={c.flag} minN={minN} points={points} />}
      </span>
      <StatusBar counts={c.counts} className="w-24 min-w-0" focusable={false} />
    </span>
  );
}

const KINDS = { step: "Steps", never: "Never rules", warning: "Warning signs" } as const;
type Kind = keyof typeof KINDS;

function RulesTable({ d, filters }: { d: VersionsResponse; filters: InsightFilters }) {
  const { ws } = useWs();
  const [kind, setKind] = useState<Kind>("step");
  const rules = d.rules.filter((r: VersionRule) => r.kind === kind);
  const names = new Map(d.procedures.map((p) => [p.procedure_id, p.procedure_name]));
  const t = d.thresholds;
  return (
    <WidgetCard
      title="Rules"
      subtitle={kind === "warning" ? "How often each warning sign showed up on each version. Warning signs come from the caller, so they're not compared." : `Pass rate per rule on each version. A version is called better or worse than the baseline only with ${t.min_n} or more results on each.`}
      action={<Segmented small label="Kind" value={kind} options={Object.entries(KINDS) as [Kind, string][]} onChange={setKind} />}
      flush
      empty={!rules.length}
      emptyMessage="No results for this kind of rule on these versions."
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-[13.5px]">
          <thead>
            <tr>
              <th className={th}>Rule</th>
              <Columns d={d} />
            </tr>
          </thead>
          <tbody>
            {rules.map((r) => (
              <tr key={`${r.procedure_id}/${r.lineage_id}`} className="hover:bg-muted/60">
                <td className={cn(td, "max-w-[380px]")}>
                  <HrefLink href={insightsHref(ws, ["insights", "procedures", r.procedure_id, "rules", r.rule_id], filters)} className="font-semibold">
                    {ruleName(r.kind, r.index)}
                  </HrefLink>
                  {d.procedures.length > 1 && <span className="text-muted-foreground ml-1.5 text-xs">{names.get(r.procedure_id) ?? r.procedure_id}</span>}
                  <div className="text-muted-foreground line-clamp-2 text-[13px]"><RuleText text={r.text} /></div>
                </td>
                {r.cells.map((c) => (
                  <td key={c.version} className={td}>
                    <RuleCell c={c} kind={r.kind} minN={t.min_n} points={t.flag_points} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </WidgetCard>
  );
}

/** Nothing to compare yet: what sopops needs, and where to read more. */
function SetUp({ d }: { d: VersionsResponse | undefined }) {
  const one = d?.versions.length === 1;
  return (
    <section className="bg-card rounded-lg border px-5 py-5">
      <div className="flex items-start gap-3">
        <FlaskConical className="text-primary mt-0.5 size-5 shrink-0" />
        <div className="grid max-w-[72ch] gap-2.5 text-[13.5px]">
          <h2 className="text-[15px] font-semibold">{one ? "Only one version so far" : "No versions to compare yet"}</h2>
          <p>
            An A/B test compares calls that ran with different settings, like a different LLM, voice or speech model, or a different release of your instructions. sopops groups calls
            with the same agent, release and settings into a version, and shows how each version follows your procedures.
          </p>
          <ul className="text-muted-foreground grid list-disc gap-1 pl-5">
            <li>
              <b className="text-foreground font-medium">LiveKit with the sopops SDK:</b> nothing to add. <code>attach</code> reports the session&apos;s LLM, speech and voice models.
            </li>
            <li>
              <b className="text-foreground font-medium">ElevenLabs and Vapi:</b> add the API key in Settings, and sopops reads each agent&apos;s models with the post-call webhook.
            </li>
            <li>
              <b className="text-foreground font-medium">Anything else:</b> set <code>sopops_test</code>, <code>sopops_llm</code> and any other <code>sopops_</code> setting on the call, for
              example as LiveKit participant attributes.
            </li>
          </ul>
          <p className="text-muted-foreground">Then split your traffic, for example 50/50 per call. Versions show here once their calls are checked.</p>
          <div>
            <Button asChild size="sm" variant="outline">
              <a href={AB_DOCS} target="_blank" rel="noreferrer">
                <BookOpen /> How to set up A/B tests
              </a>
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}

/** What a version, the baseline and a better or worse flag mean, folded away until asked for. */
function HowToRead({ minN, points }: { minN: number; points: number }) {
  return (
    <details className="text-muted-foreground text-[13px]">
      <summary className="hover:text-foreground cursor-pointer select-none">How to read this</summary>
      <ul className="mt-1.5 grid max-w-[90ch] list-disc gap-1 pl-5">
        <li>
          <b className="text-foreground font-medium">Version:</b> calls that ran with the same agent, release and settings, named by a hash of them. Hover a letter for its full id.
        </li>
        <li>
          <b className="text-foreground font-medium">Baseline:</b> version A, the one the others are measured against. Make baseline switches it.
        </li>
        <li>
          <b className="text-foreground font-medium">Better or worse:</b> only with {minN} or more checked calls on each version, a change of at least {points} points, and 95% ranges that
          don&apos;t overlap. Otherwise it says no clear change, or too few.
        </li>
      </ul>
    </details>
  );
}

export function VersionsView({ filters, onFilters, test, v, onTest, onPick }: { filters: InsightFilters; onFilters: (f: InsightFilters) => void; test?: string; v?: string[]; onTest: (t?: string) => void; onPick: (v: string[]) => void }) {
  const { ws } = useWs();
  const comparing = (v?.length ?? 0) >= 2;
  const res = useQuery({ ...q.insightsVersions(ws, filters, test, comparing ? v : undefined), ...LIVE });
  const d = res.data;
  const ready = d && d.versions.length >= 2;
  const t = d?.thresholds;
  const [back, setBack] = useState<string[]>([]);
  return (
    <InsightsShell
      view="versions"
      title="A/B tests"
      subtitle={
        <>
          Compare versions of your agents: calls with the same agent, release and settings (model, voice, prompt) are one version.{" "}
          <a className="underline underline-offset-2" href={AB_DOCS} target="_blank" rel="noreferrer">
            How to set up A/B tests
          </a>
        </>
      }
      filters={filters}
      onFilters={onFilters}
      scope={{ release: false }}
      status={<AsOf at={res.dataUpdatedAt} fetching={res.isFetching} onRefresh={() => void res.refetch()} />}
    >
      <div className="grid gap-3">
        <div className="flex flex-wrap items-center gap-3">
          {ready && (
            <Steps
              step={comparing ? 1 : 0}
              onStep={(s) => {
                if (s === 0 && comparing) {
                  setBack(v ?? []);
                  onPick([]);
                }
              }}
            />
          )}
          <Pick
            label="Test"
            value={test}
            options={[
              ...(d?.tests ?? []).map((x) => [x.name, `${x.name} · ${x.versions} versions`] as [string, string]),
              ...(test && d && !d.tests.some((x) => x.name === test) ? [[test, `${test} · no sessions`] as [string, string]] : []),
            ]}
            onChange={onTest}
            className="max-w-72"
          />
        </div>
        {res.error && !d ? (
          <WidgetCard title="A/B tests" error={res.error} onRetry={() => void res.refetch()} />
        ) : !d ? (
          <Skel className="h-48 rounded-lg" />
        ) : !ready ? (
          <SetUp d={d} />
        ) : !comparing ? (
          <PickStep d={d} initial={back} onCompare={onPick} />
        ) : (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Button
                size="sm"
                variant="ghost"
                className="-ml-2 text-xs"
                onClick={() => {
                  setBack(d.selected);
                  onPick([]);
                }}
              >
                <ArrowLeft /> Change versions
              </Button>
              <HowToRead minN={t?.min_n ?? 10} points={t?.flag_points ?? 5} />
            </div>
            <Lineup d={d} onPick={onPick} />
            {d.procedures.length ? <Chart d={d} /> : <WidgetCard title="Procedures" empty emptyMessage="No checked calls on these versions yet." />}
            {d.procedures.length > 0 && (
              <WidgetCard
                title="Procedures"
                subtitle="Pass rate per procedure on each version, with how often its goal was met and how many calls it applied to."
                action={<OutcomeLegend />}
                flush
              >
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] border-collapse text-[13.5px]">
                    <thead>
                      <tr>
                        <th className={th}>Procedure</th>
                        <Columns d={d} />
                      </tr>
                    </thead>
                    <tbody>
                      {d.procedures.map((p) => (
                        <tr key={p.procedure_id} className="hover:bg-muted/60">
                          <td className={td}>
                            <HrefLink href={insightsHref(ws, ["insights", "procedures", p.procedure_id], filters)} className="font-semibold">
                              {p.procedure_name}
                            </HrefLink>
                          </td>
                          {p.cells.map((c) => (
                            <td key={c.version} className={td}>
                              <ProcCell c={c} minN={t?.min_n ?? 10} points={t?.flag_points ?? 5} />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </WidgetCard>
            )}
            {d.procedures.length > 0 && <RulesTable d={d} filters={filters} />}
          </>
        )}
      </div>
    </InsightsShell>
  );
}
