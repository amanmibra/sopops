// A/B tests: an agent's versions side by side. A version is the sessions with the same agent, release and config
// (models, voice, prompt, ...), listed by its hash id. The versions table shows what differs; pick up to five to
// compare, lettered A to E in the order picked, and A is the baseline. Procedures and rules follow, each version against the
// baseline with the release compare's flags. With nothing to compare yet, a short setup guide and the docs link.
import { useQuery } from "@tanstack/react-query";
import { BookOpen, FlaskConical } from "lucide-react";
import { useState } from "react";
import type { InsightFilters, VersionOut, VersionProcedureCell, VersionRule, VersionRuleCell, VersionsResponse } from "@/lib/api";
import { q } from "@/lib/api";
import { SIDES, fmtPct, insightsHref, ruleName, windowPhrase } from "@/lib/insights";
import { when } from "@/lib/kinds";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { Segmented } from "../bits";
import { HrefLink } from "../href-link";
import { Button } from "../ui/button";
import { HeaderHint, Hint, OutcomeBar, OutcomeLegend, RateN, Skel, StatusBar, WidgetCard } from "./kit";
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

const LETTERS = "ABCDEFGHIJ";
/** A picked version's letter: A for the baseline, then B, C... in the order picked. Unpicked versions have none. */
const letterOf = (d: VersionsResponse, id: string) => {
  const i = d.selected.indexOf(id);
  return i < 0 ? null : LETTERS[i];
};
const shortId = (v: VersionOut) => v.id.slice(0, 8);

/** A picked version's letter, in the baseline's color for A and the candidate color otherwise; hover for its id. */
function VersionBadge({ v, letter, baseline, className }: { v: VersionOut; letter: string; baseline?: boolean; className?: string }) {
  return (
    <Hint
      focusable={false}
      tip={
        <span className="grid gap-0.5">
          <span>
            Version <span className="font-mono">{v.id}</span>
          </span>
          {v.config.llm != null && <span className="text-muted-foreground">{String(v.config.llm)}</span>}
        </span>
      }
    >
      <span className={cn("inline-flex h-5 min-w-5 shrink-0 cursor-help items-center justify-center rounded px-1 text-[11px] font-bold", SIDES[baseline ? "a" : "b"].badge, className)}>{letter}</span>
    </Hint>
  );
}

/** What a version ran with, the settings that differ first. */
function Settings({ v, differs }: { v: VersionOut; differs: string[] }) {
  const keys = [...differs, ...Object.keys(v.config).filter((k) => !differs.includes(k) && k !== "prompt")];
  if (!v.config_hash && !differs.length) return <span className="text-muted-foreground text-xs">No settings reported</span>;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[12.5px]">
      {keys.map((k) => {
        const val = settingValue(v, k);
        return (
          <div key={k} className="contents">
            <dt className={cn("text-muted-foreground whitespace-nowrap", differs.includes(k) && "text-foreground font-medium")}>{settingName(k)}</dt>
            <dd className={cn("min-w-0 truncate font-mono text-[12px]", differs.includes(k) ? "font-semibold" : "text-muted-foreground")} title={val ?? undefined}>
              {k === "prompt" && v.prompt ? (
                <Hint focusable={false} tip={<span className="block max-h-60 overflow-auto whitespace-pre-wrap">{v.prompt}</span>}>
                  <span className="cursor-help border-b border-dotted border-current">{String(v.config.prompt)}</span>
                </Hint>
              ) : (
                (val ?? <span className="text-muted-foreground font-sans">not reported</span>)
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function VersionsTable({ d, onPick }: { d: VersionsResponse; onPick: (v: string[]) => void }) {
  const picked = d.selected;
  const toggle = (id: string) => onPick(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id].slice(0, d.max_versions));
  const baseline = (id: string) => onPick([id, ...picked.filter((x) => x !== id)]);
  return (
    <WidgetCard
      title="Versions"
      subtitle={`Sessions with the same agent, release and settings are one version, named by a hash of them. Pick up to ${d.max_versions} to compare: they're lettered A, B, C in the order you pick them, and A is the baseline.`}
      help="Settings that differ between the versions you picked are in bold. sopops reads the models and voice itself where it can (the LiveKit SDK, ElevenLabs and Vapi with an API key in Settings); anything else comes from what each call reported."
      flush
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-[13.5px]">
          <thead>
            <tr>
              <th className={cn(th, "w-10")} />
              <th className={th}>Version</th>
              <th className={th}>
                <HeaderHint label="Settings" tip="What the version ran with. Bold: differs between the versions you picked." />
              </th>
              <th className={cn(th, "text-right")}>
                <HeaderHint label="Sessions" tip="In this time range; checked ones in brackets." />
              </th>
              <th className={th}>Calls</th>
              <th className={th} />
            </tr>
          </thead>
          <tbody>
            {d.versions.map((v) => {
              const on = picked.includes(v.id);
              const isBase = v.id === d.baseline;
              const letter = letterOf(d, v.id);
              return (
                <tr key={v.id} className={cn("hover:bg-muted/60", !on && "opacity-70")}>
                  <td className={td}>
                    <input
                      id={`pick-${v.id}`}
                      type="checkbox"
                      className="mt-1 size-4"
                      checked={on}
                      disabled={!on && picked.length >= d.max_versions}
                      onChange={() => toggle(v.id)}
                      aria-label={`Compare version ${shortId(v)}`}
                    />
                  </td>
                  <td className={td}>
                    <span className="inline-flex items-center gap-2">
                      {letter && <VersionBadge v={v} letter={letter} baseline={isBase} />}
                      <span className="font-mono text-[12.5px]" title={v.id}>
                        {shortId(v)}
                      </span>
                      {isBase && <span className="text-muted-foreground text-xs">baseline</span>}
                    </span>
                  </td>
                  <td className={cn(td, "max-w-[460px]")}>
                    <Settings v={v} differs={d.differs} />
                  </td>
                  <td className={cn(td, "text-right tabular-nums")}>
                    {v.sessions} <span className="text-muted-foreground">({v.checked})</span>
                  </td>
                  <td className={cn(td, "text-muted-foreground text-xs whitespace-nowrap")}>
                    {when(v.first_at)}
                    <br />
                    to {when(v.last_at)}
                  </td>
                  <td className={cn(td, "text-right")}>
                    {on && !isBase && (
                      <Button size="sm" variant="ghost" className="text-xs" onClick={() => baseline(v.id)}>
                        Make baseline
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </WidgetCard>
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
      <span className="text-muted-foreground text-[11.5px] tabular-nums">
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

function Columns({ d }: { d: VersionsResponse }) {
  const by = new Map(d.versions.map((v) => [v.id, v]));
  return (
    <>
      {d.selected.map((id) => {
        const v = by.get(id);
        return (
          <th key={id} className={th}>
            {v && <VersionBadge v={v} letter={letterOf(d, id) ?? "?"} baseline={id === d.baseline} />}
          </th>
        );
      })}
    </>
  );
}

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
                  <div className="text-muted-foreground line-clamp-2 text-[13px]">{r.text}</div>
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

/** What a version, the baseline and a better or worse flag mean, next to the numbers they explain. */
function HowToRead({ minN, points }: { minN?: number; points?: number }) {
  const items: [string, string][] = [
    ["Version", "Calls that ran with the same agent, release and settings, named by a hash of them. A change to the model, voice or prompt starts a new version. The versions you pick get letters A to E; hover a letter for its full id."],
    ["Baseline", "Version A, the one the others are measured against: usually the one already running. Each other version shows its pass rate as points above or below it. Make baseline switches it."],
    [
      "Better or worse",
      `Shown only when the difference is unlikely to be chance: ${minN ?? 10} or more checked calls on each version, a change of at least ${points ?? 5} points, and 95% ranges that don't overlap. Otherwise it says "no clear difference" or "too few to compare".`,
    ],
  ];
  return (
    <div className="bg-muted/40 grid gap-3 rounded-lg border p-4 text-[13px] sm:grid-cols-3">
      {items.map(([term, text]) => (
        <div key={term} className="grid gap-1">
          <div className="font-semibold">{term}</div>
          <p className="text-muted-foreground leading-relaxed">{text}</p>
        </div>
      ))}
    </div>
  );
}

export function VersionsView({ filters, onFilters, test, v, onTest, onPick }: { filters: InsightFilters; onFilters: (f: InsightFilters) => void; test?: string; v?: string[]; onTest: (t?: string) => void; onPick: (v: string[]) => void }) {
  const { ws } = useWs();
  const res = useQuery({ ...q.insightsVersions(ws, filters, test, v), ...LIVE });
  const d = res.data;
  const ready = d && d.versions.filter((x) => x.config_hash || x.release != null).length >= 2 && d.versions.length >= 2;
  const t = d?.thresholds;
  return (
    <InsightsShell
      view="versions"
      title="A/B tests"
      subtitle={
        <>
          Your agent&apos;s versions over {windowPhrase(filters)}: calls with the same agent, release and settings (model, voice, prompt) are one version. Each version is compared
          with the baseline, and called better or worse only when both have enough results and their 95% ranges don&apos;t overlap.{" "}
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
      <div className="grid gap-4">
        <div className="flex flex-wrap items-center gap-2">
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
          {d && d.untagged > 0 && (
            <span className="text-muted-foreground text-xs">
              {d.untagged} {d.untagged === 1 ? "session" : "sessions"} in this time range reported no settings.
            </span>
          )}
        </div>
        {res.error && !d ? (
          <WidgetCard title="A/B tests" error={res.error} onRetry={() => void res.refetch()} />
        ) : !d ? (
          <Skel className="h-48 rounded-lg" />
        ) : !ready ? (
          <SetUp d={d} />
        ) : (
          <>
            <VersionsTable d={d} onPick={onPick} />
            <HowToRead minN={t?.min_n} points={t?.flag_points} />
            <WidgetCard
              title="Procedures"
              subtitle={`Pass rate per procedure on each version, with how often its goal was met and how many calls it applied to.`}
              help={`Pass rate is followed checks out of measurable checks. Better or worse needs ${t?.min_n} or more checks on both versions, a change of at least ${t?.flag_points} points, and 95% ranges that don't overlap.`}
              action={<OutcomeLegend />}
              flush
              empty={!d.procedures.length}
              emptyMessage="No checked sessions on these versions yet."
            >
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] border-collapse text-[13.5px]">
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
            <RulesTable d={d} filters={filters} />
          </>
        )}
      </div>
    </InsightsShell>
  );
}
