// The forms: agent builder, shared instruction, procedure, group and settings.
import { useEffect, useRef, useState } from "react";
import { PlatformLabel } from "./brand-icon";
import { type AgentConfig, type GroupConfig, type InstructionConfig, type IssueOut, type PreviewResponse, type ProcedureConfig, call, localIssue, sdk } from "@/lib/api";
import { textCount } from "@/lib/diff";
import { DELIVERY, KIND, type Kind, PLATFORMS, slug } from "@/lib/kinds";
import { usePlatforms } from "@/lib/platforms";
import { useUnsavedRegistration } from "@/lib/unsaved";
import { type Step, cleanStep } from "@/lib/model";
import { prefs } from "@/lib/prefs";
import { takeRestore } from "@/lib/restore";
import { useWs } from "@/lib/workspace";
import { Banner, Counts, IssueList, Segmented, Tag } from "./bits";
import { DiffView } from "./diff-view";
import { ActionBar, AgentVars, type Dirty, Field, StepRows, VarRows, useDirty, useItemSave } from "./form";
import { useGo } from "./href-link";
import { ItemHeader, Missing, NewHeader, Page, UsedBy } from "./item-shell";
import { BlockStack } from "./block-stack";
import { SectionHead } from "./settings";
import { Input } from "./ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Textarea } from "./ui/textarea";

/** Form state for one item: its fields, unsaved-edit tracking, and a restored older version if one was picked. */
function useFields<T extends object>(kind: Kind, id: string, initial: T) {
  const [restored] = useState(() => takeRestore(kind, id));
  const [m, setM] = useState<T>(() => (restored ? { ...structuredClone(initial), ...(restored.fields as Partial<T>) } : structuredClone(initial)));
  const dirty = useDirty(!!restored);
  const { model } = useWs();
  const label = kind === "settings" ? "Settings" : id === "new" ? `the new ${KIND[kind].one.toLowerCase()}` : `${KIND[kind].one} ${model.itemName(kind, id)}`;
  useUnsavedRegistration({ kind, id, label }, dirty.dirty);
  const set = (patch: Partial<T>) => {
    setM((prev) => ({ ...prev, ...patch }));
    dirty.mark();
  };
  const notice = restored && <Banner tone="warn">Showing {restored.label.toLowerCase()}. Save draft to keep it, or leave without saving.</Banner>;
  return { m, set, dirty, notice };
}

/** Save, then go to the saved item (a new one), or continue a navigation the leave guard held. */
function useOnSave(kind: Kind, id: string, isNew: boolean, dirty: Dirty) {
  const s = useItemSave(kind, id, isNew);
  const { href } = useWs();
  const go = useGo();
  const onSave = async (data: Record<string, unknown>, note: string | null, then?: () => void) => {
    if (!(await s.save(data, note))) return;
    dirty.clear();
    if (then) then();
    else if (isNew && kind !== "settings") go(href(kind, String(data.id)), { replace: true });
  };
  return { ...s, onSave };
}

function NameField({ isNew, value, onChange, errors, help, placeholder }: { isNew: boolean; value: string; onChange: (v: string) => void; errors: IssueOut[]; help: string; placeholder: string }) {
  return (
    <Field id="id" name="Name" help={isNew ? help : undefined} errors={errors}>
      <Input id="f-id" value={value} readOnly={!isNew} className={!isNew ? "bg-muted text-muted-foreground" : ""} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}

// --- agents: the builder ---------------------------------------------------------

type AgentModel = Pick<AgentConfig, "id" | "platform" | "platform_id" | "context" | "blocks" | "variables">;
type Preview = PreviewResponse | { valid: false; issues: IssueOut[]; prompt?: null; released_prompt?: null; release?: null };

export function AgentEditor({ id }: { id: string }) {
  const { cfg } = useWs();
  const isNew = id === "new";
  const item = isNew ? undefined : cfg.agents.find((a) => a.id === id);
  if (!isNew && !item) return <Missing kind="agent" id={id} />;
  return <AgentForm key={`${id}:${item?.version}`} id={id} item={item} />;
}

function AgentForm({ id, item }: { id: string; item?: AgentConfig }) {
  const { ws, cfg, model } = useWs();
  const plats = usePlatforms();
  const isNew = !item;
  const blank: AgentModel = { id: "", platform: plats.first, platform_id: "", context: "", blocks: [], variables: {} };
  const { m, set, dirty, notice } = useFields<AgentModel>("agent", id, item ? { id: item.id, platform: item.platform, platform_id: item.platform_id, context: item.context, blocks: item.blocks, variables: item.variables } : blank);
  const { errors, banner, onSave, remove } = useOnSave("agent", id, isNew, dirty);
  const plat = PLATFORMS.find((p) => p.value === m.platform) ?? PLATFORMS[0];

  // The draft instructions, rebuilt a moment after each edit.
  const [preview, setPreview] = useState<Preview | null>(null);
  const seq = useRef(0);
  const body = { platform: m.platform, platform_id: m.platform_id || "(not set)", context: m.context, blocks: m.blocks, variables: m.variables };
  const key = JSON.stringify(body);
  useEffect(() => {
    const t = window.setTimeout(async () => {
      const mine = ++seq.current;
      const target = isNew ? m.id.trim() || "new-agent" : id;
      try {
        const r = await call(sdk.previewAgent({ path: { workspace: ws, id: target }, body }));
        if (mine === seq.current) setPreview(r);
      } catch (e) {
        if (mine === seq.current) setPreview({ valid: false, issues: [localIssue((e as Error).message)] });
      }
    }, 350);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, ws, cfg]);

  return (
    <Page wide>
      {notice}
      {isNew ? <NewHeader kind="agent" /> : <ItemHeader kind="agent" id={id} title={id} tab="" />}
      {banner}
      <div className="grid grid-cols-1 items-start gap-7 xl:grid-cols-2">
        <div className="grid min-w-0 gap-6">
          <div className="grid gap-3 md:grid-cols-[1fr_160px_1fr] md:items-end">
            <NameField isNew={isNew} value={m.id} onChange={(v) => set({ id: v })} errors={errors} help="A short id, e.g. the location. Can't be changed later." placeholder="downtown" />
            <Field id="platform" name="Platform" errors={errors}>
              <Select value={m.platform} onValueChange={(v) => set({ platform: v as AgentModel["platform"] })}>
                <SelectTrigger id="f-platform" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PLATFORMS.filter((p) => (!p.legacy && plats.has(p.value)) || p.value === item?.platform).map((p) => (
                    <SelectItem key={p.value} value={p.value}>
                      <PlatformLabel platform={p.value} />
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field id="platform_id" name={`${plat.label} ${plat.idLabel.toLowerCase()}`} errors={errors}>
              <Input id="f-platform_id" value={m.platform_id} placeholder={plat.idLabel} onChange={(e) => set({ platform_id: e.target.value })} />
            </Field>
          </div>
          <Field id="context" name="About this agent" help="Goes first in the agent's instructions. Facts only this agent needs: the location, hours, what it doesn't offer." errors={errors}>
            <Textarea id="f-context" className="min-h-28" value={m.context} onChange={(e) => set({ context: e.target.value })} />
          </Field>
          <Field id="blocks" name="Blocks" help="Shared instructions, procedures and groups, in order. Drag or use the arrows to reorder; open a block to read or edit it." errors={errors}>
            <BlockStack value={m.blocks} onChange={(blocks) => set({ blocks })} errors={errors} />
          </Field>
          <Field id="variables" name="Variables" help="Values for the {{placeholders}} in this agent's blocks. Leave one empty to use the default from Settings." errors={errors}>
            <AgentVars value={m.variables} onChange={(variables) => set({ variables })} need={model.neededVariables(m)} defaults={cfg.settings.variables} />
          </Field>
        </div>
        <div className="min-w-0 xl:sticky xl:top-[72px]">
          <PromptPreview preview={preview} here={{ kind: "agent", id: m.id || id }} />
        </div>
      </div>
      <ActionBar kind="agent" itemId={id} isNew={isNew} dirty={dirty} onSave={(note, then) => onSave({ ...m, id: isNew ? m.id.trim() : id }, note, then)} onDelete={isNew ? undefined : remove} />
    </Page>
  );
}

function PromptPreview({ preview: r, here }: { preview: Preview | null; here: { kind: Kind; id: string } }) {
  const [chosen, setChosen] = useState<"prompt" | "changes" | null>(() => (prefs.get("previewmode") as "prompt" | "changes" | null) ?? null);
  const frame = (head: React.ReactNode, body: React.ReactNode) => (
    <section className="bg-card min-w-0 overflow-hidden rounded-xl border" aria-label="Draft instructions">
      <header className="flex flex-wrap items-center gap-2 border-b px-3.5 py-2.5">{head}</header>
      {body}
    </section>
  );
  // Nothing to compare (never published, or the same as live): just the prompt, and say why there's no diff.
  if (r && r.valid && r.prompt != null && (r.released_prompt == null || r.prompt === r.released_prompt)) {
    const why = r.released_prompt == null ? "Not published yet" : `Same as live (release #${r.release})`;
    return frame(
      <>
        <b className="mr-auto">Draft instructions</b>
        <span className="text-muted-foreground text-[13px]">{why}</span>
      </>,
      <pre className="prompt max-h-[calc(100vh-170px)]">{r.prompt}</pre>,
    );
  }
  // Until someone picks, show what changes when the draft differs from the live prompt.
  const shown = chosen ?? (r && r.valid ? "changes" : "prompt");
  const errors = r ? r.issues.filter((i) => i.severity === "error") : [];
  const c = r && r.valid ? textCount(r.released_prompt, r.prompt) : null;
  return frame(
    <>
      <b className="mr-auto">Draft instructions</b>
      {shown === "prompt" && c && (c.add || c.del) ? <Counts {...c} /> : null}
      <Segmented
        small
        label="Preview"
        value={shown}
        options={[
          ["prompt", "Instructions"],
          ["changes", "Changes vs live"],
        ]}
        onChange={(v) => {
          setChosen(v);
          prefs.set("previewmode", v);
        }}
      />
    </>,
    !r ? (
      <p className="text-muted-foreground px-3.5 py-3 text-[13px]">Building…</p>
    ) : !r.valid ? (
      <div className="px-3.5 py-3">
        <Banner tone="err" className="mb-0">
          This draft can't be built yet:
          <IssueList issues={errors.length ? errors : r.issues} here={here} />
        </Banner>
      </div>
    ) : shown === "prompt" ? (
      <pre className="prompt max-h-[calc(100vh-170px)]">{r.prompt}</pre>
    ) : (
      <div className="px-3.5 py-3 [&_.dv]:max-h-[calc(100vh-220px)]">
        <DiffView before={r.released_prompt} after={r.prompt} empty="Same as the live instructions." />
      </div>
    ),
  );
}

// --- shared instructions -----------------------------------------------------------

export function InstructionEditor({ id }: { id: string }) {
  const { cfg } = useWs();
  const isNew = id === "new";
  const item = isNew ? undefined : cfg.instructions.find((b) => b.id === id);
  if (!isNew && !item) return <Missing kind="instruction" id={id} />;
  return <InstructionForm key={`${id}:${item?.version}`} id={id} item={item} />;
}

function InstructionForm({ id, item }: { id: string; item?: InstructionConfig }) {
  const isNew = !item;
  const { m, set, dirty, notice } = useFields("instruction", id, { id: item?.id ?? "", text: item?.text ?? "" });
  const { errors, banner, onSave, remove } = useOnSave("instruction", id, isNew, dirty);
  return (
    <Page>
      {notice}
      {isNew ? (
        <NewHeader kind="instruction" />
      ) : (
        <>
          <ItemHeader kind="instruction" id={id} title={id} tab="" />
          <UsedBy kind="instruction" uses={item.used_by} />
        </>
      )}
      {banner}
      <div className="grid gap-6">
        <NameField isNew={isNew} value={m.id} onChange={(v) => set({ id: v })} errors={errors} help="A short id, for example brand-voice. Letters, digits and dashes. It can't be changed later." placeholder="brand-voice" />
        <Field id="text" name="Text" help="What the agents are told. Write {{name}} for a value that differs per agent, and set it in each agent's variables." errors={errors}>
          <Textarea id="f-text" className="min-h-52" value={m.text} onChange={(e) => set({ text: e.target.value })} />
        </Field>
      </div>
      <ActionBar kind="instruction" itemId={id} isNew={isNew} dirty={dirty} onSave={(note, then) => onSave({ ...m, id: isNew ? m.id.trim() : id }, note, then)} onDelete={isNew ? undefined : remove} />
    </Page>
  );
}

// --- procedures --------------------------------------------------------------------

type ProcModel = { id: string; name: string; goal: string; when: string; guidance: string; steps: Step[]; never: Step[]; warning_signs: Step[]; delivery: ProcedureConfig["delivery"] };

export function ProcedureEditor({ id }: { id: string }) {
  const { cfg } = useWs();
  const isNew = id === "new";
  const item = isNew ? undefined : cfg.procedures.find((p) => p.id === id);
  if (!isNew && !item) return <Missing kind="procedure" id={id} />;
  return <ProcedureForm key={`${id}:${item?.version}`} id={id} item={item} />;
}

function ProcedureForm({ id, item }: { id: string; item?: ProcedureConfig }) {
  const isNew = !item;
  const blank: ProcModel = { id: "", name: "", goal: "", when: "", guidance: "", steps: [{ text: "", tool: null, required: false }], never: [], warning_signs: [], delivery: "prompt" };
  const { m, set, dirty, notice } = useFields<ProcModel>("procedure", id, item ? { id: item.id, name: item.name, goal: item.goal, when: item.when, guidance: item.guidance, steps: item.steps, never: item.never, warning_signs: item.warning_signs, delivery: item.delivery } : blank);
  const { errors, banner, onSave, remove } = useOnSave("procedure", id, isNew, dirty);
  const [idEdited, setIdEdited] = useState(!isNew);
  const d = DELIVERY.find((x) => x.value === m.delivery) ?? DELIVERY[0];
  return (
    <Page>
      {notice}
      {isNew ? (
        <NewHeader kind="procedure" />
      ) : (
        <>
          <ItemHeader kind="procedure" id={id} title={item.name} tab="" />
          <UsedBy kind="procedure" uses={item.used_by} />
        </>
      )}
      {banner}
      <div className="grid gap-6">
        <Field id="name" name="Name" help="The heading agents see, for example Large orders." errors={errors}>
          <Input id="f-name" value={m.name} onChange={(e) => set(isNew && !idEdited ? { name: e.target.value, id: slug(e.target.value) } : { name: e.target.value })} />
        </Field>
        {isNew && (
          <Field id="id" name="ID" help="Made from the name. Agents list it by this id, and tools use it to fetch the procedure, so it can't be changed later." errors={errors}>
            <Input
              id="f-id"
              value={m.id}
              onChange={(e) => {
                setIdEdited(true);
                set({ id: e.target.value });
              }}
            />
          </Field>
        )}
        <Field id="goal" name="Goal" help="The outcome that means it worked. Reviewers judge calls against it." errors={errors}>
          <Input id="f-goal" value={m.goal} placeholder="The order is placed with a pickup time the kitchen can meet." onChange={(e) => set({ goal: e.target.value })} />
        </Field>
        <Field id="when" name="When it applies" errors={errors}>
          <Input id="f-when" value={m.when} placeholder="Any order with more than 10 items." onChange={(e) => set({ when: e.target.value })} />
        </Field>
        <Field id="guidance" name="Guidance" help="Anything else the agent should know that isn't a step." errors={errors}>
          <Textarea id="f-guidance" className="min-h-28" value={m.guidance} onChange={(e) => set({ guidance: e.target.value })} />
        </Field>
        <Field id="steps" name="Steps" help="In order. Name a tool to have the agent use it, and mark it required if the call must happen." errors={errors}>
          <StepRows id="steps" steps value={m.steps} onChange={(steps) => set({ steps })} errors={errors} placeholder="What the agent does" noun="Step" />
        </Field>
        <Field id="never" name="Never" help="Things the agent must never do." errors={errors}>
          <StepRows id="never" value={m.never} onChange={(never) => set({ never })} errors={errors} placeholder="Never …" noun="Rule" />
        </Field>
        <Field id="warning_signs" name="Warning signs" help="Situations that need special handling, usually a transfer to a person." errors={errors}>
          <StepRows id="warning_signs" value={m.warning_signs} onChange={(warning_signs) => set({ warning_signs })} errors={errors} placeholder="Caller mentions …; transfer to …" noun="Warning sign" />
        </Field>
        <Field id="delivery" name="Delivery" help={d.help} errors={errors}>
          <Select value={m.delivery} onValueChange={(v) => set({ delivery: v as ProcModel["delivery"] })}>
            <SelectTrigger id="f-delivery" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DELIVERY.map((x) => (
                <SelectItem key={x.value} value={x.value}>
                  {x.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      </div>
      <ActionBar
        kind="procedure"
        itemId={id}
        isNew={isNew}
        dirty={dirty}
        onSave={(note, then) =>
          onSave({ ...m, id: isNew ? m.id.trim() : id, steps: m.steps.map(cleanStep), never: m.never.map(cleanStep), warning_signs: m.warning_signs.map(cleanStep) }, note, then)
        }
        onDelete={isNew ? undefined : remove}
      />
    </Page>
  );
}

// --- groups ------------------------------------------------------------------------

export function GroupEditor({ id }: { id: string }) {
  const { cfg } = useWs();
  const isNew = id === "new";
  const item = isNew ? undefined : cfg.groups.find((g) => g.id === id);
  if (!isNew && !item) return <Missing kind="group" id={id} />;
  return <GroupForm key={`${id}:${item?.version}`} id={id} item={item} />;
}

function GroupForm({ id, item }: { id: string; item?: GroupConfig }) {
  const isNew = !item;
  const { m, set, dirty, notice } = useFields("group", id, { id: item?.id ?? "", blocks: item?.blocks ?? [] });
  const { errors, banner, onSave, remove } = useOnSave("group", id, isNew, dirty);
  return (
    <Page>
      {notice}
      {isNew ? (
        <NewHeader kind="group" />
      ) : (
        <>
          <ItemHeader kind="group" id={id} title={id} tab="" />
          <UsedBy kind="group" uses={item.used_by} />
        </>
      )}
      {banner}
      <div className="grid gap-6">
        <NameField
          isNew={isNew}
          value={m.id}
          onChange={(v) => set({ id: v })}
          errors={errors}
          help="A short id, for example delivery-orders. It shares names with shared instructions and procedures, and can't be changed later."
          placeholder="delivery-orders"
        />
        <Field id="blocks" name="Blocks" help="In order. An agent that lists this group gets these blocks in its place. Groups can include other groups." errors={errors}>
          <BlockStack value={m.blocks} onChange={(blocks) => set({ blocks })} errors={errors} owner={isNew ? undefined : id} />
        </Field>
      </div>
      <ActionBar kind="group" itemId={id} isNew={isNew} dirty={dirty} onSave={(note, then) => onSave({ id: isNew ? m.id.trim() : id, blocks: m.blocks }, note, then)} onDelete={isNew ? undefined : remove} />
    </Page>
  );
}

// --- settings ----------------------------------------------------------------------

export function SettingsEditor() {
  const { cfg } = useWs();
  return <SettingsForm key={String(cfg.settings.version)} />;
}

function SettingsForm() {
  const { cfg, changeOf } = useWs();
  const { m, set, dirty, notice } = useFields("settings", "settings", { variables: cfg.settings.variables, procedures_heading: cfg.settings.procedures_heading });
  const { errors, banner, onSave } = useOnSave("settings", "settings", false, dirty);
  const c = changeOf("settings", "settings");
  return (
    <Page>
      {notice}
      <SectionHead title="Set the defaults every agent's instructions use" aside={c && <Tag tone={c.change}>{c.change}, not published</Tag>}>
        Changes here are saved as drafts and go live when you publish, like any other edit.
      </SectionHead>
      {banner}
      <div className="grid gap-6">
        <Field id="variables" name="Default variables" help="Values for placeholders every agent gets unless the agent sets its own." errors={errors}>
          <VarRows id="variables" value={m.variables} onChange={(variables) => set({ variables })} placeholder="default value" />
        </Field>
        <Field id="procedures_heading" name="Procedures heading" help="Placed once, right before the first procedure in every agent's instructions. Leave it empty for none." errors={errors}>
          <Input id="f-procedures_heading" value={m.procedures_heading} onChange={(e) => set({ procedures_heading: e.target.value })} />
        </Field>
      </div>
      <ActionBar kind="settings" itemId="settings" isNew={false} dirty={dirty} onSave={(note, then) => onSave({ variables: m.variables, procedures_heading: m.procedures_heading }, note, then)} />
    </Page>
  );
}
