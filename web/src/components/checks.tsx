// SOP check results on a session: each procedure's result and rule statuses, with evidence that points into the transcript,
// and the controls to correct a rule's status.
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import type { ProcedureCheckOut, RuleResultOut, SessionSummary } from "@/lib/api";
import { RULE_STATUS } from "@/lib/review";
import { sessionsHref } from "@/lib/insights";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Note, RuleText, Tag } from "./bits";
import { CheckMetadata } from "./check-metadata";
import { FindingOverrideControl } from "./finding-override";
import { HrefLink } from "./href-link";
import { CorrectionNote, RuleCorrectionControls } from "./rule-correction";

export type Result = NonNullable<SessionSummary["result"]>;

export const RESULT: Record<Result, { label: string; tone: string; help: string }> = {
  deviated: { label: "deviated", tone: "bg-err-soft text-err border-transparent", help: "A step was skipped or a never rule was broken." },
  partial: { label: "partial", tone: "bg-warn-soft text-warn border-transparent", help: "Steps out of order or not reached, or a step skipped on the way to the goal." },
  followed: { label: "followed", tone: "bg-brand-soft text-primary border-transparent", help: "Every applicable step done in order, no never rule broken." },
  not_measurable: { label: "not measurable", tone: "", help: "Nothing to judge: no applicable step and no never rule broken." },
};

export function ResultTag({ result, big }: { result: Result | null | undefined; big?: boolean }) {
  if (!result) return <span className="text-muted-foreground">—</span>;
  const r = RESULT[result];
  return (
    <span title={r.help}>
      <Tag big={big} className={r.tone}>
        {r.label}
      </Tag>
    </span>
  );
}

type Status = RuleResultOut["status"];

const STATUS = RULE_STATUS;

const KIND: Record<RuleResultOut["kind"], string> = { step: "Step", never: "Never", warning: "Warning sign" };

export const ruleLabel = (r: RuleResultOut) => `${KIND[r.kind]} ${r.index}`;
export const ruleKey = (procedureId: string, r: RuleResultOut) => `${procedureId}/${r.rule_id}`;

/** Turn index → the rules whose evidence cites it. */
export type TurnMarks = Map<number, { key: string; label: string; status: Status; finding: boolean; quotes: string[] }[]>;

export function turnMarks(checks: ProcedureCheckOut[]): TurnMarks {
  const marks: TurnMarks = new Map();
  for (const c of checks) {
    for (const r of c.rules) {
      for (const e of r.evidence) {
        if (e.turn == null) continue;
        const list = marks.get(e.turn) ?? [];
        const key = ruleKey(c.procedure_id, r);
        const found = list.find((m) => m.key === key);
        if (found) found.quotes.push(e.quote);
        else list.push({ key, label: `${c.procedure_name ?? c.procedure_id}: ${ruleLabel(r)}`, status: r.status, finding: r.finding && !r.suppressed, quotes: [e.quote] });
        marks.set(e.turn, list);
      }
    }
  }
  return marks;
}

/** The text with the quotes marked (case-insensitive, first match each). */
export function Highlighted({ text, quotes, strong }: { text: string; quotes: string[]; strong: boolean }) {
  const ranges: [number, number][] = [];
  const lower = text.toLowerCase();
  for (const q of quotes) {
    const i = q ? lower.indexOf(q.toLowerCase()) : -1;
    if (i >= 0) ranges.push([i, i + q.length]);
  }
  if (!ranges.length) return <>{text}</>;
  ranges.sort((a, b) => a[0] - b[0]);
  const out: React.ReactNode[] = [];
  let at = 0;
  ranges.forEach(([s, e], n) => {
    if (s < at) s = at;
    if (e <= s) return;
    out.push(text.slice(at, s));
    out.push(
      <mark key={n} className={cn("rounded-sm px-0.5 text-inherit", strong ? "bg-warn-soft ring-warn/40 ring-1" : "bg-brand-soft")}>
        {text.slice(s, e)}
      </mark>,
    );
    at = e;
  });
  out.push(text.slice(at));
  return <>{out}</>;
}

function RuleRow({ sessionId, procedureId, rule, active, onSelect }: { sessionId: string; procedureId: string; rule: RuleResultOut; active: boolean; onSelect: (key: string | null, turn?: number | null) => void }) {
  const s = STATUS[rule.status];
  const key = ruleKey(procedureId, rule);
  const first = rule.evidence.find((e) => e.turn != null)?.turn;
  const { ws } = useWs();
  return (
    <li className={cn("group rounded-md border border-transparent px-2 py-2", active && "border-border bg-muted", rule.finding && !rule.suppressed && !active && "bg-err-soft/40", rule.suppressed && "opacity-70")}>
      <div className="flex items-start gap-1">
        <button type="button" className="grid min-w-0 flex-1 grid-cols-[88px_minmax(0,1fr)] gap-2 text-left" onClick={() => onSelect(active ? null : key, first)} aria-expanded={active}>
          <span className="pt-px">
            <Tag className={rule.suppressed ? "line-through" : s.tone}>{s.label}</Tag>
          </span>
          <span className="text-sm">
            <span className="text-muted-foreground">{ruleLabel(rule)}.</span> <RuleText text={rule.text} />
          </span>
        </button>
        {rule.finding && rule.finding_id && <FindingOverrideControl findingId={rule.finding_id} override={rule.override} kind={rule.kind} />}
      </div>
      <div className="ml-[96px] min-w-0">
        <CorrectionNote rule={rule} className="mt-1" />
        <RuleCorrectionControls sessionId={sessionId} procedureId={procedureId} rule={rule} className={cn("-ml-2 mt-0.5", !active && "hidden group-hover:flex")} />
      </div>
      {rule.suppressed && (
        <p className="text-muted-foreground mt-0.5 ml-[96px] text-xs">
          Suppressed: {rule.override === false ? (rule.correction && rule.correction.source !== "override" ? "a reviewer corrected its status" : "a reviewer marked this finding incorrect") : "a verdict on its pattern hides it (acceptable here, or the checker was wrong)"}. Left out of the result and of Insights.
        </p>
      )}
      {active && (
        <div className="mt-2 ml-[96px] grid gap-2 text-sm">
          {rule.reason && <p className="text-muted-foreground">{rule.reason}</p>}
          {rule.evidence.map((e, i) => (
            <button
              type="button"
              key={i}
              className="hover:bg-card rounded-md border px-2 py-1 text-left"
              onClick={() => e.turn != null && onSelect(key, e.turn)}
              disabled={e.turn == null}
              title={e.turn != null ? `Show turn ${e.turn} in the transcript` : undefined}
            >
              <span className="text-muted-foreground mr-2 font-mono text-xs">{e.source === "tool_calls" ? "tool call" : `#${e.turn}`}</span>“{e.quote}”
            </button>
          ))}
          {rule.pattern_id && (
            <span className="text-sm">
              Pattern:{" "}
              <HrefLink href={wsHref(ws, "patterns", rule.pattern_id)} title="This pattern: its verdict, examples and suggestions">
                {rule.pattern_name ?? "this pattern"}
              </HrefLink>{" "}
              ·{" "}
              <HrefLink href={sessionsHref(ws, { pattern: rule.pattern_id })} title="Every session in this pattern">
                sessions
              </HrefLink>
            </span>
          )}
          <span className="text-muted-foreground font-mono text-sm">rule {rule.rule_id}</span>
        </div>
      )}
    </li>
  );
}

function ProcedureCard({ sessionId, check, activeKey, onSelect }: { sessionId: string; check: ProcedureCheckOut; activeKey: string | null; onSelect: (key: string | null, turn?: number | null) => void }) {
  const checked = check.result != null;
  const [open, setOpen] = useState(checked);
  const state = !check.applicable ? "didn't apply" : check.excluded_reason ? "excluded" : checked ? null : "not checked yet";
  return (
    <section className="border-b py-2 last:border-b-0">
      <button type="button" className="flex w-full items-center gap-2 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}
        <span className="flex-1 font-semibold">{check.procedure_name ?? check.procedure_id}</span>
        {state ? (
          <Tag>{state}</Tag>
        ) : (
          <span className="flex items-center gap-2">
            {check.checked_result && check.checked_result !== check.result && (
              <span className="opacity-60" title="The checker's own result, before suppressed findings were left out">
                <ResultTag result={check.checked_result} />
              </span>
            )}
            {check.checked_result && check.checked_result !== check.result && <span className="text-muted-foreground text-xs">→</span>}
            <ResultTag result={check.result} />
          </span>
        )}
      </button>
      {open && (
        <div className="mt-2 pl-6">
          {(check.excluded_reason || check.route_reason) && <p className="text-muted-foreground mb-2 text-sm">{check.excluded_reason ?? check.route_reason}</p>}
          {checked && (
            <p className="mb-2 text-sm">
              Goal{" "}
              {check.goal_reached == null ? <Tag>unclear</Tag> : check.goal_reached ? <Tag className="text-ok border-transparent bg-transparent">reached</Tag> : <Tag className="bg-err-soft text-err border-transparent">not reached</Tag>}
              {check.goal_reason && <span className="text-muted-foreground"> {check.goal_reason}</span>}
            </p>
          )}
          {check.rules.length > 0 && (
            <ul className="grid gap-0.5">
              {check.rules.map((r) => (
                <RuleRow key={r.rule_id} sessionId={sessionId} procedureId={check.procedure_id} rule={r} active={activeKey === ruleKey(check.procedure_id, r)} onSelect={onSelect} />
              ))}
            </ul>
          )}
          {checked && (
            <CheckMetadata model={check.model} promptVersion={check.prompt_version} releaseId={check.release_id} />
          )}
        </div>
      )}
    </section>
  );
}

export function ChecksPanel({ sessionId, checks, activeKey, onSelect }: { sessionId: string; checks: ProcedureCheckOut[]; activeKey: string | null; onSelect: (key: string | null, turn?: number | null) => void }) {
  if (!checks.length) return <Note>Not checked against any procedure yet.</Note>;
  return (
    <div>
      {checks.map((c) => (
        <ProcedureCard key={c.procedure_id} sessionId={sessionId} check={c} activeKey={activeKey} onSelect={onSelect} />
      ))}
    </div>
  );
}
