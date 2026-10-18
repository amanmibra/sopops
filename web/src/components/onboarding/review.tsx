// Import agents, step 2: progress (and the kickoff for Claude Code or Codex), then review the proposal and apply it as drafts.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import type {
  AgentFidelity, EventOut, Finding, IssueOut, ImportOut, Kickoff, Proposal, ProposalContent, ProposalIssue, ProposedAgent, ProposedInstruction, ProposedProcedure,
} from "@/client/types.gen";
import { call, refresh, sdk } from "@/lib/api";
import { platformName, plural, when } from "@/lib/kinds";
import { TERMINAL, iq, kickoffFor, pct } from "@/lib/onboarding";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ConfirmButton, ErrorBanner, KindBadge, Note, PageTitle, Panel, Segmented, Sub, Tag } from "../bits";
import { DiffView } from "../diff-view";
import { Field, StepRows, VarRows, author } from "../form";
import { HrefLink, useGo } from "../href-link";
import { Page } from "../item-shell";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Textarea } from "../ui/textarea";

const STATUS: Record<ImportOut["status"], string> = {
  waiting: "waiting for your agent",
  running: "working",
  ready: "ready to review",
  failed: "failed",
  applied: "applied as drafts",
  discarded: "discarded",
};

export function ImportView({ id }: { id: string }) {
  const { ws } = useWs();
  const imp = useQuery(iq.one(ws, id));
  if (imp.isPending) return <Page>Loading…</Page>;
  if (imp.isError)
    return (
      <Page>
        <ErrorBanner error={imp.error} />
      </Page>
    );
  const d = imp.data;
  const live = !TERMINAL.includes(d.status) && d.status !== "failed" && (d.status !== "ready" || !!d.token?.active);
  const kickoff = kickoffFor(id);
  return (
    <Page wide>
      <PageTitle aside={<Tag tone={d.status === "failed" ? "removed" : d.status === "ready" ? "live" : undefined}>{STATUS[d.status]}</Tag>}>Import agents</PageTitle>
      <Sub>
        {d.source === "agent" ? "Claude Code or Codex extracts the instructions in your repo and sends them here." : "sopops is extracting the instructions with its model."}{" "}
        {d.agents.length ? `${plural(d.agents.length, "agent", "agents")}: ${d.agents.join(", ")}.` : ""}
        {d.mode === "repo" && d.repo ? ` Repo mode: ${d.repo}.` : ""}
      </Sub>
      {d.source === "agent" && !TERMINAL.includes(d.status) && <TokenPanel imp={d} kickoff={kickoff?.kickoff} token={kickoff?.token} />}
      <ProgressLog id={id} live={live} />
      {d.status === "failed" && (
        <Banner tone="err">
          {d.error || "The import failed."} <HrefLink href={wsHref(ws, "import")}>Start again</HrefLink>
        </Banner>
      )}
      {d.status === "applied" && (
        <Banner tone="ok">
          Applied as drafts. <HrefLink href={wsHref(ws, "changes")}>Review the unpublished changes</HrefLink>, then publish.
        </Banner>
      )}
      {d.status === "discarded" && <Banner>This import was discarded. Nothing in {ws} changed.</Banner>}
      {d.proposal && d.status === "ready" && <Review key={`${d.pushes}:${d.updated_at}`} imp={d} proposal={d.proposal} />}
    </Page>
  );
}

// --- progress and kickoff ----------------------------------------------------------------------

function ProgressLog({ id, live }: { id: string; live: boolean }) {
  const { ws } = useWs();
  const events = useQuery(iq.events(ws, id, live));
  const rows: EventOut[] = events.data ?? [];
  return (
    <Panel title="Progress">
      {rows.length ? (
        <ol className="grid max-h-64 gap-1 overflow-auto text-[13px]" aria-live="polite">
          {rows.map((e) => (
            <li key={e.id} className="grid grid-cols-[72px_80px_1fr] gap-2">
              <span className="text-muted-foreground tabular-nums">{new Date(e.at).toLocaleTimeString()}</span>
              <span className={cn("font-semibold", e.by === "agent" ? "text-k-procedure" : "text-muted-foreground")}>{e.by === "agent" ? "your agent" : "sopops"}</span>
              <span>{e.message}</span>
            </li>
          ))}
        </ol>
      ) : (
        <Note>Nothing yet.</Note>
      )}
    </Panel>
  );
}

function CopyBlock({ label, text, rows }: { label: string; text: string; rows?: number }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setDone(true);
    setTimeout(() => setDone(false), 1500);
  };
  return (
    <div className="mb-3">
      <div className="mb-1 flex items-center gap-2">
        <span className="font-semibold">{label}</span>
        <Button size="xs" variant="outline" onClick={copy} aria-label={`Copy ${label}`}>
          {done ? <Check /> : <Copy />} {done ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre className="bg-muted overflow-x-auto rounded-md px-3 py-2 font-mono text-[12.5px] whitespace-pre-wrap break-all" style={rows ? { maxHeight: `${rows * 1.5}em` } : undefined}>
        {text}
      </pre>
    </div>
  );
}

function EditableCopy({ label, initial, help }: { label: string; initial: string; help?: string }) {
  const [text, setText] = useState(initial);
  const [done, setDone] = useState(false);
  return (
    <div className="mb-3">
      <div className="mb-1 flex items-center gap-2">
        <span className="font-semibold">{label}</span>
        <Button
          size="xs"
          variant="outline"
          onClick={async () => {
            await navigator.clipboard.writeText(text);
            setDone(true);
            setTimeout(() => setDone(false), 1500);
          }}
        >
          {done ? <Check /> : <Copy />} {done ? "Copied" : "Copy"}
        </Button>
      </div>
      {help && <p className="text-muted-foreground mb-1.5 text-[13px]">{help}</p>}
      <Textarea aria-label={label} className="font-mono text-[12.5px]" value={text} onChange={(e) => setText(e.target.value)} />
    </div>
  );
}

function TokenPanel({ imp, kickoff, token }: { imp: ImportOut; kickoff?: Kickoff; token?: string }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [tool, setTool] = useState<"claude" | "codex">("claude");
  const t = imp.token;
  const finish = async () => {
    await call(sdk.revokeImportToken({ path: { workspace: ws, id: imp.id } }));
    await qc.invalidateQueries({ queryKey: ["ws", ws, "import", imp.id] });
    toast.success("The onboarding token no longer works.");
  };
  if (!t) return null;
  if (!t.active)
    return (
      <Panel title="Onboarding token">
        <Note>
          The token {t.revoked_at ? "was ended" : "expired"}. Your agent can't send more. {imp.proposal ? "Review what it sent below." : ""}{" "}
          <HrefLink href={wsHref(ws, "import")}>Start a new one</HrefLink>
        </Note>
      </Panel>
    );
  return (
    <Panel
      title={
        <span className="flex flex-wrap items-center gap-2">
          Start your coding agent
          <Note>
            token {t.prefix}…, works until {when(t.expires_at)}
          </Note>
          <span className="flex-1" />
          <ConfirmButton size="sm" variant="outline" confirm="End the token?" onConfirm={finish}>
            Finish
          </ConfirmButton>
        </span>
      }
    >
      {kickoff && token ? (
        <details open={!imp.proposal}>
          <summary className="text-muted-foreground mb-2 cursor-pointer text-[13px]">{imp.proposal ? "Commands and prompt" : "How to start"}</summary>
          <p className="mb-3 max-w-[80ch] text-[13px]">
            In a terminal at the root of your agents' repo, run this. It adds the sopops tools to your coding agent and starts it on the onboarding instructions. The token in it is shown only now.
          </p>
          <div className="mb-3">
            <Segmented
              label="Coding agent"
              value={tool}
              onChange={setTool}
              options={[
                ["claude", "Claude Code"],
                ["codex", "Codex"],
              ]}
            />
          </div>
          <CopyBlock label="Run in your agents' repo" text={tool === "claude" ? kickoff.claude_code : kickoff.codex} />
          {tool === "codex" && (
            <details className="mb-3">
              <summary className="text-muted-foreground cursor-pointer text-[13px]">Or add the server to ~/.codex/config.toml</summary>
              <CopyBlock label="config.toml" text={kickoff.codex_toml} />
            </details>
          )}
          <EditableCopy label="Recommended settings" initial={kickoff.settings} help="Suggestions; edit them for your setup." />
        </details>
      ) : (
        <Note>
          The commands and token were shown when the token was created. Lost them? <HrefLink href={wsHref(ws, "import")}>Create a new token</HrefLink> (end this one with Finish).
        </Note>
      )}
    </Panel>
  );
}

// --- review --------------------------------------------------------------------------------------

type Draft = Required<ProposalContent>;

const content = (p: Proposal | ProposalContent): Draft => ({
  agents: p.agents ?? [],
  instructions: p.instructions ?? [],
  procedures: p.procedures ?? [],
  groups: p.groups ?? [],
  settings: p.settings ?? { variables: {}, procedures_heading: "## Procedures" },
});

function Review({ imp, proposal }: { imp: ImportOut; proposal: Proposal }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const [draft, setDraft] = useState<Draft>(() => content(proposal));
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tab, setTab] = useState<"blocks" | "agents">("blocks");
  useEffect(() => setDraft(content(proposal)), [proposal]);

  const edit = (next: Draft) => {
    setDraft(next);
    setDirty(true);
  };
  const reload = () => qc.invalidateQueries({ queryKey: ["ws", ws, "import", imp.id] });
  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  const save = () =>
    act("save", async () => {
      await call(sdk.updateImportProposal({ path: { workspace: ws, id: imp.id }, body: draft }));
      setDirty(false);
      await reload();
    });
  const decide = (finding: string, choice: string) =>
    act(`decide:${finding}`, async () => {
      await call(sdk.resolveImportFinding({ path: { workspace: ws, id: imp.id, finding }, body: { choice } }));
      await reload();
    });
  const apply = () =>
    act("apply", async () => {
      const res = await call(sdk.applyImport({ path: { workspace: ws, id: imp.id }, body: { proposal: dirty ? draft : null, author: author() } }));
      await refresh(qc, ws);
      toast.success(`Saved as drafts: ${plural(res.changed.length, "file", "files")}.`, { description: res.replaced.length ? `Replaced: ${res.replaced.join(", ")}` : undefined });
      go(wsHref(ws, "changes"));
    });
  const discard = () =>
    act("discard", async () => {
      await call(sdk.discardImport({ path: { workspace: ws, id: imp.id } }));
      await reload();
    });

  const fidelity = proposal.fidelity ?? [];
  const findings = proposal.findings ?? [];
  const drift = findings.filter((f) => f.kind === "drift");
  const merged = findings.filter((f) => f.kind !== "drift");
  const errors = (proposal.issues ?? []).filter((i) => i.severity === "error");
  const lost = fidelity.filter((f) => !f.ok);
  const names = useMemo(() => new Map<string, string>([...draft.procedures.map((p) => [p.id, p.name] as const), ...draft.instructions.map((i) => [i.id, i.id] as const)]), [draft]);

  return (
    <>
      <Panel title="What was found">
        <div className="flex flex-wrap gap-x-6 gap-y-1">
          <span>{plural(draft.agents.length, "agent", "agents")}</span>
          <span>{plural(draft.instructions.length, "shared instruction", "shared instructions")}</span>
          <span>{plural(draft.procedures.length, "procedure", "procedures")}</span>
          {draft.groups.length > 0 && <span>{plural(draft.groups.length, "group", "groups")}</span>}
          <span>{plural(drift.length, "difference to decide", "differences to decide")}</span>
          <span>
            Fidelity:{" "}
            {fidelity.length ? (
              lost.length ? (
                <b className="text-err">{plural(lost.length, "agent loses", "agents lose")} text</b>
              ) : (
                <b className="text-ok">every agent keeps all of its text</b>
              )
            ) : (
              "not checked"
            )}
          </span>
        </div>
      </Panel>
      {errors.length > 0 && (
        <Banner tone="err">
          sopc can't build this yet:
          <ul>
            {errors.map((i, n) => (
              <li key={n}>
                {i.id ? `${i.id}: ` : ""}
                {i.text || i.message}
              </li>
            ))}
          </ul>
        </Banner>
      )}
      {drift.length > 0 && (
        <Panel title="Where agents differ">
          <p className="text-muted-foreground mb-3 text-[13px]">Until you decide, each agent keeps what it says today.{dirty ? " Save your edits before deciding." : ""}</p>
          <div className="grid gap-3">
            {drift.map((f) => (
              <DriftCard key={f.id} f={f} disabled={dirty || !!busy} onDecide={(choice) => decide(f.id, choice)} />
            ))}
          </div>
        </Panel>
      )}
      {merged.length > 0 && (
        <details className="bg-card mb-4 rounded-xl border px-4 py-3">
          <summary className="cursor-pointer font-semibold">Merged across agents ({merged.length})</summary>
          <ul className="mt-2 grid gap-1.5 text-[13px]">
            {merged.map((f) => (
              <li key={f.id}>
                <Tag>{f.kind === "duplicate" ? "same text" : "values"}</Tag> {f.message}
                {f.kind === "values" && (
                  <span className="text-muted-foreground">
                    {" "}
                    ({(f.variants ?? []).map((v) => `${v.agents.join(", ")}: "${v.text}"`).join("; ")})
                  </span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      {(proposal.notes ?? []).length > 0 && (
        <Banner tone="warn">
          Notes from the import:
          <ul>
            {proposal.notes!.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </Banner>
      )}
      <div className="mb-3">
        <Segmented
          label="Review"
          value={tab}
          onChange={setTab}
          options={[
            ["blocks", `Blocks (${draft.instructions.length + draft.procedures.length})`],
            ["agents", `Agents (${draft.agents.length})`],
          ]}
        />
      </div>
      {tab === "blocks" ? (
        <div className="grid gap-4">
          {draft.instructions.map((it, i) => (
            <InstructionCard
              key={it.id}
              it={it}
              issues={issuesFor(proposal, "instruction", it.id)}
              onChange={(next) => edit({ ...draft, instructions: draft.instructions.map((x, j) => (j === i ? next : x)) })}
            />
          ))}
          {draft.procedures.map((p, i) => (
            <ProcedureCard
              key={p.id}
              p={p}
              issues={issuesFor(proposal, "procedure", p.id)}
              onChange={(next) => edit({ ...draft, procedures: draft.procedures.map((x, j) => (j === i ? next : x)) })}
            />
          ))}
          {draft.groups.map((g) => (
            <Panel key={g.id} title={<span className="flex items-center gap-2"><KindBadge kind="group" /> {g.id}</span>} className="mb-0">
              <Note>Every agent starts with these blocks, so they're listed once as a group: {g.blocks.map((b) => names.get(b) ?? b).join(", ")}.</Note>
            </Panel>
          ))}
        </div>
      ) : (
        <div className="grid gap-4">
          {draft.agents.map((a, i) => (
            <AgentCard
              key={a.id}
              a={a}
              names={names}
              groups={draft.groups}
              fidelity={fidelity.find((f) => f.agent === a.id)}
              original={proposal.originals?.[a.id]}
              stale={dirty}
              onChange={(next) => edit({ ...draft, agents: draft.agents.map((x, j) => (j === i ? next : x)) })}
            />
          ))}
        </div>
      )}
      {error ? <ErrorBanner error={error} lead="That didn't work:" /> : null}
      <div className="bg-card sticky bottom-0 z-10 -mx-4 mt-6 flex flex-wrap items-center gap-2 border-t px-4 py-3 md:-mx-8 md:px-8">
        <Button onClick={apply} disabled={!!busy || errors.length > 0}>
          {busy === "apply" ? "Applying…" : "Apply as drafts"}
        </Button>
        <Button variant="outline" onClick={save} disabled={!dirty || !!busy}>
          {busy === "save" ? "Checking…" : "Save and check fidelity"}
        </Button>
        <Note>{dirty ? "Edits not checked yet." : "Nothing is published: you'll review the drafts in Unpublished changes."}</Note>
        <span className="flex-1" />
        <ConfirmButton variant="ghost" confirm="Discard the import?" onConfirm={discard} disabled={!!busy}>
          Discard
        </ConfirmButton>
      </div>
    </>
  );
}

function issuesFor(p: Proposal, kind: string, id: string): ProposalIssue[] {
  return (p.issues ?? []).filter((i) => i.kind === kind && i.id === id);
}

const asIssues = (issues: ProposalIssue[]): IssueOut[] =>
  issues.map((i) => ({ code: i.code, message: i.message, path: i.path ?? "", severity: (i.severity === "warning" ? "warning" : "error") as "error" | "warning", kind: (i.kind ?? null) as IssueOut["kind"], id: i.id ?? null, field: i.field ?? null, index: i.index ?? null, text: i.text ?? null }));

function DriftCard({ f, disabled, onDecide }: { f: Finding; disabled: boolean; onDecide: (choice: string) => void }) {
  return (
    <div className={cn("rounded-lg border px-3.5 py-3", f.resolved ? "bg-muted" : "bg-warn-soft border-transparent")}>
      <div className="mb-2 font-medium">{f.message}</div>
      <div className="grid gap-2">
        {(f.variants ?? []).map((v, i) => (
          <div key={i} className="bg-card flex flex-wrap items-start gap-3 rounded-md border px-3 py-2">
            <div className="min-w-0 flex-1">
              <div className="text-muted-foreground mb-0.5 text-[12px]">{v.agents.join(", ")}</div>
              <div className="text-[13px] whitespace-pre-wrap">{v.text}</div>
            </div>
            {!f.resolved && (
              <Button size="sm" variant="outline" disabled={disabled} onClick={() => onDecide(v.agents[0])}>
                Use for every agent
              </Button>
            )}
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-2">
        {f.resolved ? (
          <Note>{f.resolved === "keep" ? "Decided: each agent keeps its version." : `Decided: every agent uses ${f.resolved}'s version.`}</Note>
        ) : (
          <Button size="sm" variant="ghost" disabled={disabled} onClick={() => onDecide("keep")}>
            Keep {(f.variants ?? []).length > 2 ? "each version" : "both"}
          </Button>
        )}
      </div>
    </div>
  );
}

function Users({ ids }: { ids: string[] }) {
  return <Note>{ids.length ? `Used by ${ids.join(", ")}` : "No agent uses it"}</Note>;
}

function InstructionCard({ it, issues, onChange }: { it: ProposedInstruction; issues: ProposalIssue[]; onChange: (it: ProposedInstruction) => void }) {
  return (
    <Panel className="mb-0" title={<span className="flex flex-wrap items-center gap-2"><KindBadge kind="instruction" /> {it.id} <Users ids={it.used_by ?? []} /></span>}>
      <Field id="text" name="Text" errors={asIssues(issues)}>
        <Textarea aria-label={`${it.id} text`} className="min-h-20" value={it.text} onChange={(e) => onChange({ ...it, text: e.target.value })} />
      </Field>
    </Panel>
  );
}

function ProcedureCard({ p, issues, onChange }: { p: ProposedProcedure; issues: ProposalIssue[]; onChange: (p: ProposedProcedure) => void }) {
  const errors = asIssues(issues);
  const set = (x: Partial<ProposedProcedure>) => onChange({ ...p, ...x });
  return (
    <Panel className="mb-0" title={<span className="flex flex-wrap items-center gap-2"><KindBadge kind="procedure" /> {p.name} <Note>{p.id}</Note> <Users ids={p.used_by ?? []} /></span>}>
      <div className="grid gap-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Field id="name" name="Name" errors={errors}>
            <Input aria-label={`${p.id} name`} value={p.name} onChange={(e) => set({ name: e.target.value })} />
          </Field>
          <Field id="when" name="When it applies" errors={errors}>
            <Input aria-label={`${p.id} when`} value={p.when ?? ""} onChange={(e) => set({ when: e.target.value })} />
          </Field>
        </div>
        <Field
          id="goal"
          name="Goal"
          help={p.goal_inferred ? <span className="text-warn">Written by the import: the original didn't state one. Check it says what success means.</span> : undefined}
          errors={errors}
        >
          <Input aria-label={`${p.id} goal`} value={p.goal ?? ""} onChange={(e) => set({ goal: e.target.value, goal_inferred: false })} />
        </Field>
        {(p.guidance ?? "") !== "" && (
          <Field id="guidance" name="Guidance" errors={errors}>
            <Textarea aria-label={`${p.id} guidance`} value={p.guidance ?? ""} onChange={(e) => set({ guidance: e.target.value })} />
          </Field>
        )}
        <Field id="steps" name="Steps" errors={errors}>
          <StepRows id="steps" steps value={p.steps ?? []} onChange={(steps) => set({ steps })} errors={errors} placeholder="What the agent does" noun="Step" />
        </Field>
        <Field id="never" name="Never" errors={errors}>
          <StepRows id="never" value={p.never ?? []} onChange={(never) => set({ never })} errors={errors} placeholder="Never …" noun="Rule" />
        </Field>
        <Field id="warning_signs" name="Warning signs" errors={errors}>
          <StepRows id="warning_signs" value={p.warning_signs ?? []} onChange={(warning_signs) => set({ warning_signs })} errors={errors} placeholder="Caller mentions …; transfer to …" noun="Warning sign" />
        </Field>
      </div>
    </Panel>
  );
}

const lostOf = (f: AgentFidelity) => (f.missing ?? []).length + (f.changed ?? []).length;

function FidelityTag({ f }: { f?: AgentFidelity }) {
  if (!f) return <Tag>not checked</Tag>;
  if (f.ok) return <Tag tone="live">{pct(f.score)} of {f.sentences} sentences kept</Tag>;
  return <Tag tone="removed">{lostOf(f)} of {f.sentences} sentences lost or changed</Tag>;
}

function AgentCard({
  a, names, groups, fidelity: f, original, stale, onChange,
}: {
  a: ProposedAgent;
  names: Map<string, string>;
  groups: Draft["groups"];
  fidelity?: AgentFidelity;
  original?: string;
  stale: boolean;
  onChange: (a: ProposedAgent) => void;
}) {
  const [diff, setDiff] = useState(false);
  const blockName = (b: string) => {
    const g = groups?.find((x) => x.id === b);
    return g ? `${b} (${g.blocks.map((x) => names.get(x) ?? x).join(", ")})` : (names.get(b) ?? b);
  };
  return (
    <Panel
      className="mb-0"
      title={
        <span className="flex flex-wrap items-center gap-2">
          {a.id} <Note>{platformName(a.platform ?? "livekit")}</Note> <FidelityTag f={f} />
          {stale && <Note>(edits not checked yet)</Note>}
        </span>
      }
    >
      <div className="grid gap-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Field id="platform_id" name="Platform id" help="The agent's id on its platform." errors={[]}>
            <Input aria-label={`${a.id} platform id`} value={a.platform_id ?? ""} onChange={(e) => onChange({ ...a, platform_id: e.target.value })} />
          </Field>
          <Field id="blocks" name="Blocks, in prompt order" errors={[]}>
            <ol className="list-decimal pl-5 text-[13px]">
              {(a.blocks ?? []).map((b) => (
                <li key={b}>{blockName(b)}</li>
              ))}
            </ol>
          </Field>
        </div>
        <Field id="context" name="The agent's own text" help="Facts about this agent only. It goes first in its prompt." errors={[]}>
          <Textarea aria-label={`${a.id} context`} value={a.context ?? ""} onChange={(e) => onChange({ ...a, context: e.target.value })} />
        </Field>
        {Object.keys(a.variables ?? {}).length > 0 && (
          <Field id="variables" name="Variables" help="This agent's values for the {{placeholders}} in shared blocks." errors={[]}>
            <VarRows id={`vars-${a.id}`} value={a.variables ?? {}} onChange={(variables) => onChange({ ...a, variables })} placeholder="value" />
          </Field>
        )}
        {f && lostOf(f) > 0 && (
          <Banner tone="err" className="mb-0">
            Lost or changed compared with the original:
            <ul>
              {(f.missing ?? []).map((m, i) => (
                <li key={`m${i}`}>Missing: {m}</li>
              ))}
              {(f.changed ?? []).map((c, i) => (
                <li key={`c${i}`}>
                  Changed: {c.original} <span className="text-muted-foreground">now:</span> {c.now}
                </li>
              ))}
            </ul>
          </Banner>
        )}
        {f && (
          <div>
            <Button size="sm" variant="outline" onClick={() => setDiff(!diff)}>
              {diff ? "Hide" : "Compare with"} the original prompt
            </Button>
            {(f.reworded ?? []).length > 0 && <Note className="ml-2">{plural((f.reworded ?? []).length, "sentence", "sentences")} reworded into procedure steps</Note>}
            {diff && (
              <div className="mt-3">
                <DiffView before={original ?? ""} after={f.compiled ?? ""} empty="The compiled prompt is the original, word for word." />
              </div>
            )}
          </div>
        )}
      </div>
    </Panel>
  );
}

export function ImportCard({ children }: { children?: ReactNode }) {
  const { ws } = useWs();
  return (
    <Panel title="Import agents">
      <p className="mb-3 text-[13px]">
        Bring existing prompts into {ws}: from Claude Code or Codex in your repo, pasted or uploaded files, ElevenLabs or Vapi. You review the result before it's saved as drafts.
      </p>
      {children}
      <HrefLink href={wsHref(ws, "import")}>Import agents</HrefLink>
    </Panel>
  );
}
