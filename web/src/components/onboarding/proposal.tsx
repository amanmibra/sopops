// Reviewing an import's proposal, one named step at a time: what was found (the blocks picture, filled in), the blocks
// and agents to read and edit, the variables, text only one agent has, what the coding agent noticed, where agents
// disagree, and saving it all as drafts. Steps with nothing in them are left out.
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, ChevronRight } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import type {
  AgentFidelity, IssueOut, ImportOut, Proposal, ProposalContent, ProposalIssue, ProposedAgent, ProposedInstruction, ProposedProcedure,
} from "@/client/types.gen";
import { call, refresh, sdk } from "@/lib/api";
import { plural } from "@/lib/kinds";
import { pct } from "@/lib/onboarding";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ConfirmButton, ErrorBanner, KindBadge, Note, Panel, Segmented, Tag } from "../bits";
import { PlatformLabel } from "../brand-icon";
import { DiffView } from "../diff-view";
import { Field, StepRows, VarRows, author } from "../form";
import { useGo } from "../href-link";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Textarea } from "../ui/textarea";
import { BlocksDiagram, type DiagramAgent } from "./blocks-diagram";
import { Disagreements, Unique, Variables, useReview } from "./summary";

export type StepId = "found" | "blocks" | "variables" | "unique" | "notes" | "disagree" | "save";
const ORDER: StepId[] = ["found", "blocks", "variables", "unique", "notes", "disagree", "save"];

/** Where the review is, kept by the import page so it survives the review reloading after a save. */
export type ReviewPlace = { step: StepId; reached: StepId; focus: boolean };

/** Who made the proposal, as the import page names it: the coding agent's product, or sopops' own model. */
export const madeBy = (imp: ImportOut) => (imp.source === "agent" ? (imp.agent_client?.label ?? "your coding agent") : "sopops");
export const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

const agentAnchor = (id: string) => `import-agent-${id}`;

type Draft = Required<ProposalContent>;

const AGENTS_PAGE = 20;
const FOUND_AGENTS = 4;

const content = (p: Proposal | ProposalContent): Draft => ({
  agents: p.agents ?? [],
  instructions: p.instructions ?? [],
  procedures: p.procedures ?? [],
  groups: p.groups ?? [],
  settings: p.settings ?? { variables: {}, procedures_heading: "## Procedures" },
});

const failingOf = (fidelity: AgentFidelity[]) => fidelity.filter((f) => !f.ok || f.score < 1);

export function Review({ imp, proposal, place, onPlace }: { imp: ImportOut; proposal: Proposal; place: ReviewPlace; onPlace: (p: ReviewPlace) => void }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const review = useReview(ws, imp.id);
  const [draft, setDraft] = useState<Draft>(() => content(proposal));
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tab, setTab] = useState<"blocks" | "agents">("blocks");
  const [agentPage, setAgentPage] = useState(0);
  const [opened, setOpened] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
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
      return true;
    } catch (e) {
      setError(e);
      return false;
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
  const errors = (proposal.issues ?? []).filter((i) => i.severity === "error");
  const notes = proposal.notes ?? [];
  const names = useMemo(() => new Map<string, string>([...draft.procedures.map((p) => [p.id, p.name] as const), ...draft.instructions.map((i) => [i.id, i.id] as const)]), [draft]);
  const r = review.r;
  const who = madeBy(imp);

  const steps: { id: StepId; name: string }[] = [
    { id: "found" as const, name: "What we found" },
    { id: "blocks" as const, name: "Read the blocks" },
    ...(r?.variables.length ? [{ id: "variables" as const, name: "Fill in the blanks" }] : []),
    ...(r?.unique.length ? [{ id: "unique" as const, name: "Text only one agent has" }] : []),
    ...(notes.length ? [{ id: "notes" as const, name: `What ${who} noticed` }] : []),
    ...(r?.disagreements.length ? [{ id: "disagree" as const, name: "Decide where agents disagree" }] : []),
    { id: "save" as const, name: "Save as drafts" },
  ];
  const rank = (s: StepId) => ORDER.indexOf(s);
  // A step that has nothing in it any more (a reload emptied it) gives way to the next one that does.
  const current = steps.find((s) => rank(s.id) >= rank(place.step)) ?? steps[steps.length - 1];
  const at = steps.indexOf(current);
  const next = steps[at + 1];
  const prev = steps[at - 1];

  const moveTo = async (to: StepId) => {
    if (dirty && rank(to) > rank("blocks") && !(await save())) return;
    onPlace({ step: to, reached: rank(to) > rank(place.reached) ? to : place.reached, focus: true });
  };
  const openAgent = (id: string) => {
    const i = draft.agents.findIndex((a) => a.id === id);
    setTab("agents");
    if (i >= 0) setAgentPage(Math.floor(i / AGENTS_PAGE));
    setOpened(id);
    onPlace({ step: "blocks", reached: rank("blocks") > rank(place.reached) ? "blocks" : place.reached, focus: false });
  };

  // Changing steps moves focus to the new step's heading, or to the agent that was opened from the picture.
  useEffect(() => {
    if (opened && current.id === "blocks") {
      const el = document.getElementById(agentAnchor(opened));
      el?.scrollIntoView({ block: "start" });
      el?.focus({ preventScroll: true });
      return;
    }
    if (place.focus) {
      heading.current?.focus({ preventScroll: true });
      heading.current?.scrollIntoView({ block: "start" });
      onPlace({ ...place, focus: false });
    }
  }, [current.id, opened, place.focus]);

  const off = dirty || !!review.busy;
  const disagreements = r?.disagreements.length ?? 0;
  const title: Record<StepId, string> = {
    found: "What we found",
    blocks: "Read the blocks",
    variables: "Fill in the blanks",
    unique: `Text only one agent has (${r?.unique.length ?? 0})`,
    notes: `What ${who} noticed`,
    disagree: disagreements === 1 ? "Agents disagree on 1 rule. Pick one wording for it." : `Agents disagree on ${disagreements} rules. Pick one wording for each.`,
    save: "Save as drafts",
  };

  return (
    <>
      <Stepper steps={steps} current={current.id} reached={place.reached} onPick={(s) => void moveTo(s)} />
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
      <section aria-labelledby="review-step-heading">
        <h2 id="review-step-heading" ref={heading} tabIndex={-1} className="mb-1.5 scroll-mt-40 md:scroll-mt-20 text-lg leading-snug font-semibold outline-none">
          {title[current.id]}
        </h2>
        {review.q.error ? <ErrorBanner error={review.q.error} lead="The review couldn't be loaded:" /> : null}
        {review.error ? <ErrorBanner error={review.error} lead="That didn't work:" /> : null}
        {current.id === "found" && <Found draft={draft} fidelity={fidelity} onAgent={openAgent} />}
        {current.id === "blocks" && (
          <BlocksStep
            draft={draft}
            proposal={proposal}
            names={names}
            fidelity={fidelity}
            dirty={dirty}
            busy={busy}
            tab={tab}
            onTab={setTab}
            agentPage={agentPage}
            onAgentPage={setAgentPage}
            opened={opened}
            onSave={() => void save()}
            onEdit={edit}
          />
        )}
        {current.id === "variables" && r && (
          <>
            <Intro>
              A variable is a blank in shared text that each agent fills in its own way. For example, every agent says “You're the host at {"{restaurant_name}"}”, and each
              one fills in its own name.
            </Intro>
            <Variables rows={r.variables} disabled={off} onEdit={review.editVariable} />
          </>
        )}
        {current.id === "unique" && r && (
          <>
            <Intro>No other agent has this text. It stays in that agent's own text unless you make it a block.</Intro>
            <Unique items={r.unique} disabled={off} onDecide={review.decide} />
          </>
        )}
        {current.id === "notes" && (
          <>
            <Intro>{capital(who)} left these notes while it read your agents' instructions.</Intro>
            <Panel>
              <ul className="grid list-disc gap-1.5 pl-5">
                {notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </Panel>
          </>
        )}
        {current.id === "disagree" && r && (
          <>
            <Intro>
              Agents word a rule differently here: a different number, or a “never” or “must” that others don't have. Until you pick, each agent keeps the wording it
              has today.
            </Intro>
            <Disagreements items={r.disagreements} disabled={off} onDecide={review.decide} />
          </>
        )}
        {current.id === "save" && <SaveStep draft={draft} variables={r?.variables.length ?? 0} undecided={r?.counts.undecided ?? 0} onDecide={() => void moveTo("disagree")} />}
      </section>
      {error ? <ErrorBanner error={error} lead="That didn't work:" /> : null}
      <div className="bg-card sticky bottom-0 z-10 -mx-4 mt-6 flex flex-wrap items-center gap-2 border-t px-4 py-3 md:-mx-8 md:px-8">
        {prev && (
          <Button variant="ghost" className="-ml-2" disabled={!!busy} onClick={() => void moveTo(prev.id)}>
            <ArrowLeft /> Back
          </Button>
        )}
        {current.id === "blocks" && dirty && <Note>Edits not checked yet.</Note>}
        <span className="flex-1" />
        <ConfirmButton variant="ghost" confirm="Discard the import?" onConfirm={discard} disabled={!!busy}>
          Discard
        </ConfirmButton>
        {next ? (
          <Button className="ml-auto" onClick={() => void moveTo(next.id)} disabled={!!busy}>
            {busy === "save" ? "Checking…" : "Continue"} <ArrowRight />
          </Button>
        ) : (
          <Button className="ml-auto" onClick={() => void apply()} disabled={!!busy || errors.length > 0}>
            {busy === "apply" ? "Applying…" : "Apply as drafts"}
          </Button>
        )}
      </div>
    </>
  );
}

function Intro({ children }: { children: ReactNode }) {
  return <p className="text-muted-foreground mb-3 max-w-[75ch] text-[13px] leading-relaxed">{children}</p>;
}

/** The review's steps by name: earlier ones (and any already reached) can be picked, later ones can't yet. */
function Stepper({ steps, current, reached, onPick }: { steps: { id: StepId; name: string }[]; current: StepId; reached: StepId; onPick: (s: StepId) => void }) {
  const last = ORDER.indexOf(reached);
  const nav = useRef<HTMLElement>(null);
  // On a narrow screen the names scroll sideways: keep the current one in view.
  useEffect(() => {
    const el = nav.current?.querySelector<HTMLElement>("[aria-current=step]");
    if (nav.current && el) nav.current.scrollLeft = el.offsetLeft - (nav.current.clientWidth - el.offsetWidth) / 2;
  }, [current]);
  return (
    <nav ref={nav} aria-label="Review steps" className="relative mb-4 overflow-x-auto border-b">
      <ol className="flex min-w-max items-center gap-x-1 gap-y-0.5 pb-2 text-[13px] sm:min-w-0 sm:flex-wrap">
        {steps.map((s, i) => {
          const here = s.id === current;
          const open = !here && ORDER.indexOf(s.id) <= last;
          return (
            <li key={s.id} className="flex items-center gap-1">
              {i > 0 && <ChevronRight className="text-muted-foreground size-3.5" aria-hidden />}
              {open ? (
                <button type="button" className="text-primary cursor-pointer rounded px-1.5 py-1 hover:underline" onClick={() => onPick(s.id)}>
                  {s.name}
                </button>
              ) : (
                <span aria-current={here ? "step" : undefined} className={cn("rounded px-1.5 py-1", here ? "bg-brand-soft font-semibold" : "text-muted-foreground")}>
                  {s.name}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

// --- What we found -------------------------------------------------------------------------------------

function Found({ draft, fidelity, onAgent }: { draft: Draft; fidelity: AgentFidelity[]; onAgent: (id: string) => void }) {
  const [all, setAll] = useState(false);
  const groups = new Set(draft.groups.map((g) => g.id));
  const procedures = new Set(draft.procedures.map((p) => p.id));
  const shown = all ? draft.agents : draft.agents.slice(0, FOUND_AGENTS);
  const agents: DiagramAgent[] = shown.map((a) => ({
    id: a.id,
    platform: a.platform ?? "livekit",
    context: a.context,
    blocks: (a.blocks ?? []).map((b) => ({ id: b, kind: groups.has(b) ? "group" : procedures.has(b) ? "procedure" : "instruction" })),
  }));
  const compiled = new Map(fidelity.map((f) => [f.agent, f.compiled ?? ""]));
  const prompts = shown
    .filter((a) => compiled.get(a.id))
    .map((a) => ({ agent: a.id, lines: compiled.get(a.id)!.split("\n").filter((l) => l.trim()).slice(0, 7) }));
  const failing = failingOf(fidelity);
  return (
    <>
      <Intro>Click an agent to read its full instructions.</Intro>
      <Panel>
        <BlocksDiagram
          blocks={{
            instructions: draft.instructions.map((i) => i.id),
            procedures: draft.procedures.map((p) => p.id),
            groups: draft.groups.map((g) => ({ id: g.id, members: g.blocks })),
          }}
          agents={agents}
          prompts={prompts}
          onAgent={onAgent}
          more={
            draft.agents.length > FOUND_AGENTS && (
              <Button size="xs" variant="outline" className="justify-self-start" onClick={() => setAll(!all)}>
                {all ? "Show fewer" : `Show all ${draft.agents.length} agents`}
              </Button>
            )
          }
          footer={
            fidelity.length === 0 ? null : failing.length === 0 ? (
              <span className="text-ok font-semibold">Every agent keeps all of its text.</span>
            ) : (
              <span>
                <b className="text-err">{failing.length === 1 ? "1 agent loses or changes text" : `${failing.length} agents lose or change text`}</b>:{" "}
                {failing.map((f, i) => (
                  <span key={f.agent}>
                    {i > 0 && ", "}
                    <button type="button" className="text-primary cursor-pointer font-semibold hover:underline" onClick={() => onAgent(f.agent)}>
                      {f.agent}
                    </button>
                  </span>
                ))}
                .
              </span>
            )
          }
        />
      </Panel>
    </>
  );
}

// --- Read the blocks ------------------------------------------------------------------------------------

function BlocksStep({
  draft, proposal, names, fidelity, dirty, busy, tab, onTab, agentPage, onAgentPage, opened, onSave, onEdit,
}: {
  draft: Draft;
  proposal: Proposal;
  names: Map<string, string>;
  fidelity: AgentFidelity[];
  dirty: boolean;
  busy: string | null;
  tab: "blocks" | "agents";
  onTab: (t: "blocks" | "agents") => void;
  agentPage: number;
  onAgentPage: (p: number) => void;
  opened: string | null;
  onSave: () => void;
  onEdit: (d: Draft) => void;
}) {
  return (
    <>
      <Intro>
        These are the blocks sopops made from your agents' instructions, and the agents that use them. Fix anything that reads wrong. Before you go on, sopops checks your edits
        against the original instructions.
      </Intro>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Segmented
          label="Review"
          value={tab}
          onChange={onTab}
          options={[
            ["blocks", `Blocks (${draft.instructions.length + draft.procedures.length})`],
            ["agents", `Agents (${draft.agents.length})`],
          ]}
        />
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={onSave} disabled={!dirty || !!busy}>
          {busy === "save" ? "Checking…" : "Save and check fidelity"}
        </Button>
      </div>
      {tab === "blocks" ? (
        <div className="grid gap-4">
          {draft.instructions.map((it, i) => (
            <InstructionCard
              key={it.id}
              it={it}
              issues={issuesFor(proposal, "instruction", it.id)}
              onChange={(next) => onEdit({ ...draft, instructions: draft.instructions.map((x, j) => (j === i ? next : x)) })}
            />
          ))}
          {draft.procedures.map((p, i) => (
            <ProcedureCard
              key={p.id}
              p={p}
              issues={issuesFor(proposal, "procedure", p.id)}
              onChange={(next) => onEdit({ ...draft, procedures: draft.procedures.map((x, j) => (j === i ? next : x)) })}
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
          {draft.agents.length > AGENTS_PAGE && (
            <div className="flex items-center gap-2 text-[13px]">
              <Button size="xs" variant="outline" disabled={agentPage === 0} onClick={() => onAgentPage(agentPage - 1)}>
                Previous
              </Button>
              <span className="text-muted-foreground">
                Agents {agentPage * AGENTS_PAGE + 1}–{Math.min((agentPage + 1) * AGENTS_PAGE, draft.agents.length)} of {draft.agents.length}
              </span>
              <Button size="xs" variant="outline" disabled={(agentPage + 1) * AGENTS_PAGE >= draft.agents.length} onClick={() => onAgentPage(agentPage + 1)}>
                Next
              </Button>
            </div>
          )}
          {draft.agents.map((a, i) => (i < agentPage * AGENTS_PAGE || i >= (agentPage + 1) * AGENTS_PAGE ? null : (
            <AgentCard
              key={a.id}
              a={a}
              names={names}
              groups={draft.groups}
              fidelity={fidelity.find((f) => f.agent === a.id)}
              original={proposal.originals?.[a.id]}
              stale={dirty}
              open={opened === a.id}
              onChange={(next) => onEdit({ ...draft, agents: draft.agents.map((x, j) => (j === i ? next : x)) })}
            />
          )))}
        </div>
      )}
    </>
  );
}

// --- Save as drafts --------------------------------------------------------------------------------------

function SaveStep({ draft, variables, undecided, onDecide }: { draft: Draft; variables: number; undecided: number; onDecide: () => void }) {
  const blocks = [
    plural(draft.instructions.length, "shared instruction", "shared instructions"),
    plural(draft.procedures.length, "procedure", "procedures"),
    ...(draft.groups.length ? [plural(draft.groups.length, "group", "groups")] : []),
  ];
  return (
    <>
      <Intro>Applying saves the import as drafts. Nothing goes live until you publish.</Intro>
      <Panel>
        <ul className="grid gap-1.5">
          <li>
            <b>{plural(draft.agents.length, "agent", "agents")}</b>: {draft.agents.map((a) => a.id).slice(0, 8).join(", ")}
            {draft.agents.length > 8 ? ` and ${draft.agents.length - 8} more` : ""}
          </li>
          <li>
            <b>Blocks</b>: {blocks.join(", ")}
          </li>
          <li>
            <b>{plural(variables, "variable", "variables")}</b>
          </li>
        </ul>
      </Panel>
      {undecided > 0 && (
        <Banner tone="warn">
          {undecided === 1 ? "1 disagreement isn't decided yet." : `${undecided} disagreements aren't decided yet.`} Each agent keeps the wording it has today.{" "}
          <button type="button" className="cursor-pointer font-semibold underline underline-offset-2" onClick={onDecide}>
            Decide them
          </button>
        </Banner>
      )}
    </>
  );
}


function issuesFor(p: Proposal, kind: string, id: string): ProposalIssue[] {
  return (p.issues ?? []).filter((i) => i.kind === kind && i.id === id);
}

const asIssues = (issues: ProposalIssue[]): IssueOut[] =>
  issues.map((i) => ({ code: i.code, message: i.message, path: i.path ?? "", severity: (i.severity === "warning" ? "warning" : "error") as "error" | "warning", kind: (i.kind ?? null) as IssueOut["kind"], id: i.id ?? null, field: i.field ?? null, index: i.index ?? null, text: i.text ?? null }));

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
  a, names, groups, fidelity: f, original, stale, open, onChange,
}: {
  a: ProposedAgent;
  open?: boolean;
  names: Map<string, string>;
  groups: Draft["groups"];
  fidelity?: AgentFidelity;
  original?: string;
  stale: boolean;
  onChange: (a: ProposedAgent) => void;
}) {
  const [diff, setDiff] = useState(!!open);
  const blockName = (b: string) => {
    const g = groups?.find((x) => x.id === b);
    return g ? `${b} (${g.blocks.map((x) => names.get(x) ?? x).join(", ")})` : (names.get(b) ?? b);
  };
  return (
    <div id={agentAnchor(a.id)} tabIndex={-1} className="scroll-mt-40 md:scroll-mt-20 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring">
    <Panel
      className="mb-0"
      title={
        <span className="flex flex-wrap items-center gap-2">
          {a.id} <Note><PlatformLabel platform={a.platform ?? "livekit"} size={12} /></Note> <FidelityTag f={f} />
          {stale && <Note>(edits not checked yet)</Note>}
        </span>
      }
    >
      <div className="grid gap-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Field id="platform_id" name="Platform id" help="The agent's id on its platform." errors={[]}>
            <Input aria-label={`${a.id} platform id`} value={a.platform_id ?? ""} onChange={(e) => onChange({ ...a, platform_id: e.target.value })} />
          </Field>
          <Field id="blocks" name="Blocks, in order" errors={[]}>
            <ol className="list-decimal pl-5 text-[13px]">
              {(a.blocks ?? []).map((b) => (
                <li key={b}>{blockName(b)}</li>
              ))}
            </ol>
          </Field>
        </div>
        <Field id="context" name="The agent's own text" help="Facts about this agent only. It goes first in its instructions." errors={[]}>
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
              {diff ? "Hide" : "Compare with"} the original instructions
            </Button>
            {(f.reworded ?? []).length > 0 && <Note className="ml-2">{plural((f.reworded ?? []).length, "sentence", "sentences")} reworded into procedure steps</Note>}
            {diff && (
              <div className="mt-3">
                <DiffView before={original ?? ""} after={f.compiled ?? ""} empty="The full instructions match the original, word for word." />
              </div>
            )}
          </div>
        )}
      </div>
    </Panel>
    </div>
  );
}

