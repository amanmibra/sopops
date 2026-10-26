// Form pieces: a field with its errors, rows of steps or variables, the save bar, and saving and deleting an item.
import { useQueryClient } from "@tanstack/react-query";
import { useBlocker } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, CircleHelp, X } from "lucide-react";
import { type ReactNode, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError, type IssueOut, call, localIssue, refresh, sdk } from "@/lib/api";
import { signedIn } from "@/lib/auth";

type Saving = Promise<{ data?: { changed: unknown[] }; error?: unknown; response?: Response }>;
import { ITEM_ID, KIND, type Kind, type ListKind, plural } from "@/lib/kinds";
import type { Step } from "@/lib/model";
import { prefs } from "@/lib/prefs";
import { cn } from "@/lib/utils";
import { useUnsaved } from "@/lib/unsaved";
import { useWs } from "@/lib/workspace";
import { Banner, ConfirmButton, ErrorBanner } from "./bits";
import { useGo } from "./href-link";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "./ui/tooltip";

export type Errors = IssueOut[];
export const errsFor = (errors: Errors, key: string, index?: number) =>
  errors.filter((i) => i.field === key && (index === undefined ? i.index === null || i.index === undefined : i.index === index));

export function ErrNotes({ errors, field, index }: { errors: Errors; field: string; index?: number }) {
  return (
    <>
      {errsFor(errors, field, index).map((i, n) => (
        <div key={n} className={cn("mt-2 text-sm", i.severity === "warning" ? "text-warn" : "text-err")}>
          {i.text || i.message}
        </div>
      ))}
    </>
  );
}

export function Field({
  id,
  name,
  help,
  helpPosition = "before",
  errors,
  children,
}: {
  id: string;
  name: string;
  help?: ReactNode;
  helpPosition?: "before" | "tooltip";
  errors: Errors;
  children: ReactNode;
}) {
  const bad = errsFor(errors, id).some((i) => i.severity !== "warning");
  return (
    <div className={cn("min-w-0", bad && "[&_input]:border-err [&_textarea]:border-err [&_button[role=combobox]]:border-err")} data-field={id}>
      <div className="mb-0.5 flex items-center gap-1.5">
        <label className="font-semibold" htmlFor={`f-${id}`}>{name}</label>
        {help && helpPosition === "tooltip" && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <button type="button" aria-label={`Help for ${name}`} className="text-muted-foreground hover:text-foreground focus-visible:outline-ring inline-flex cursor-help focus-visible:outline-2 focus-visible:outline-offset-2">
                  <CircleHelp className="size-3.5" aria-hidden="true" />
                </button>
              </TooltipTrigger>
              <TooltipContent className="max-w-72 text-left" sideOffset={6}>{help}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>
      {help && helpPosition === "before" && <p className="text-muted-foreground mb-2 max-w-[72ch] text-sm">{help}</p>}
      {children}
      <ErrNotes errors={errors} field={id} />
    </div>
  );
}

const iconBtn = "text-muted-foreground hover:text-foreground";

/** Ordered rows of text with an optional tool, and "required" for steps. */
export function StepRows({
  id,
  value,
  onChange,
  errors,
  steps = false,
  placeholder,
  noun,
}: {
  id: string;
  value: Step[];
  onChange: (v: Step[]) => void;
  errors: Errors;
  steps?: boolean;
  placeholder: string;
  noun: string;
}) {
  const set = (i: number, s: Partial<Step>) => onChange(value.map((x, j) => (j === i ? { ...x, ...s } : x)));
  const move = (i: number, d: number) => {
    const list = [...value];
    list.splice(i + d, 0, list.splice(i, 1)[0]);
    onChange(list);
  };
  const lower = noun.toLowerCase();
  return (
    <div id={`f-${id}`}>
      <div className="grid gap-2">
        {value.map((s, i) => {
          const bad = errsFor(errors, id, i).length > 0;
          return (
            <div key={i} className={cn("grid items-center gap-2", steps ? "grid-cols-[26px_1fr_170px_auto_auto] max-md:grid-cols-[22px_1fr_auto]" : "grid-cols-[1fr_170px_auto] max-md:grid-cols-[1fr_auto]", bad && "[&_input]:border-err")}>
              {steps && <span className="text-muted-foreground text-right tabular-nums">{i + 1}.</span>}
              <Input value={s.text} placeholder={placeholder} aria-label={`${noun} ${i + 1}`} onChange={(e) => set(i, { text: e.target.value })} data-step-text />
              <Input
                className="max-md:col-start-2"
                value={s.tool ?? ""}
                placeholder="Tool (optional)"
                aria-label={`Tool for ${lower} ${i + 1}`}
                onChange={(e) => set(i, { tool: e.target.value.trim() ? e.target.value : null })}
              />
              {steps && (
                <label className="inline-flex cursor-pointer items-center gap-2" title="The agent must call the tool">
                  <Checkbox checked={!!s.required} onCheckedChange={(c) => set(i, { required: c === true })} />
                  Required
                </label>
              )}
              <span className="flex">
                <Button variant="ghost" size="icon-sm" className={iconBtn} type="button" disabled={i === 0} aria-label={`Move ${lower} ${i + 1} up`} onClick={() => move(i, -1)}>
                  <ArrowUp />
                </Button>
                <Button variant="ghost" size="icon-sm" className={iconBtn} type="button" disabled={i === value.length - 1} aria-label={`Move ${lower} ${i + 1} down`} onClick={() => move(i, 1)}>
                  <ArrowDown />
                </Button>
                <Button variant="ghost" size="icon-sm" className={iconBtn} type="button" aria-label={`Remove ${lower} ${i + 1}`} onClick={() => onChange(value.filter((_, j) => j !== i))}>
                  <X />
                </Button>
              </span>
              <div className={cn("col-span-full", steps && "col-start-2")}>
                <ErrNotes errors={errors} field={id} index={i} />
              </div>
            </div>
          );
        })}
      </div>
      <Button
        variant="outline"
        size="sm"
        type="button"
        className="mt-2"
        onClick={(e) => {
          onChange([...value, { text: "", tool: null, required: false }]);
          const box = (e.currentTarget as HTMLElement).parentElement;
          setTimeout(() => {
            const texts = box?.querySelectorAll<HTMLInputElement>("[data-step-text]");
            texts?.[texts.length - 1]?.focus();
          }, 0);
        }}
      >
        + Add {lower}
      </Button>
    </div>
  );
}

type VarRow = { k: string; v: string; fixed?: boolean };
const toVars = (rows: VarRow[]) => {
  const out: Record<string, string> = {};
  for (const r of rows) {
    const k = r.k.trim();
    if (k && !(r.fixed && r.v === "")) out[k] = r.v;
  }
  return out;
};

function RemoveVar({ name, onClick }: { name: string; onClick: () => void }) {
  return (
    <Button variant="ghost" size="icon-sm" className={iconBtn} type="button" aria-label={`Remove ${name || "variable"}`} onClick={onClick}>
      <X />
    </Button>
  );
}

function AddVar({ onClick }: { onClick: (e: React.MouseEvent<HTMLButtonElement>) => void }) {
  return (
    <Button variant="outline" size="sm" type="button" className="mt-2" onClick={onClick}>
      + Add variable
    </Button>
  );
}

const focusLast = (e: React.MouseEvent<HTMLElement>) => {
  const box = e.currentTarget.parentElement;
  setTimeout(() => {
    const inputs = box?.querySelectorAll<HTMLInputElement>("[data-var-row]:last-of-type input");
    inputs?.[0]?.focus();
  }, 0);
};

/** Name/value rows. Rows keep their own order and blanks while editing; the value is rebuilt from them. */
export function VarRows({ id, value, onChange, placeholder }: { id: string; value: Record<string, string>; onChange: (v: Record<string, string>) => void; placeholder: string }) {
  const [rows, setRows] = useState<VarRow[]>(() => Object.entries(value).map(([k, v]) => ({ k, v })));
  const update = (next: VarRow[]) => {
    setRows(next);
    onChange(toVars(next));
  };
  const names = rows.map((r) => r.k.trim());
  return (
    <div id={`f-${id}`}>
      <div className="grid gap-2">
        {rows.map((r, i) => {
          const dup = r.k.trim() && names.indexOf(r.k.trim()) !== i;
          return (
            <div key={i} data-var-row className={cn("grid grid-cols-[minmax(120px,220px)_1fr_auto] items-center gap-2", dup && "[&_input]:border-err")}>
              <Input value={r.k} placeholder="name" aria-label="Variable name" onChange={(e) => update(rows.map((x, j) => (j === i ? { ...x, k: e.target.value } : x)))} />
              <Input value={r.v} placeholder={placeholder} aria-label={`Value of ${r.k || "variable"}`} onChange={(e) => update(rows.map((x, j) => (j === i ? { ...x, v: e.target.value } : x)))} />
              <RemoveVar name={r.k} onClick={() => update(rows.filter((_, j) => j !== i))} />
              {dup && <div className="text-err col-span-full text-sm">This name is used twice; the last value wins.</div>}
            </div>
          );
        })}
      </div>
      <AddVar
        onClick={(e) => {
          setRows([...rows, { k: "", v: "" }]);
          focusLast(e);
        }}
      />
    </div>
  );
}

/** An agent's variables: the ones its blocks need come first (unset ones flagged), then any others it sets. */
export function AgentVars({
  value,
  onChange,
  need,
  defaults,
}: {
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
  need: Map<string, string[]>;
  defaults: Record<string, string>;
}) {
  const [stored, setStored] = useState<VarRow[]>(() => Object.entries(value).map(([k, v]) => ({ k, v })));
  // Needed names without a row get one, fixed to that name; fixed rows nobody needs any more go away.
  let rows = stored.filter((r) => !r.fixed || need.has(r.k) || r.v !== "");
  for (const name of need.keys()) if (!rows.some((r) => r.k.trim() === name)) rows = [...rows, { k: name, v: "", fixed: true }];
  const order = [...need.keys()];
  const rank = (r: VarRow) => (need.has(r.k.trim()) ? order.indexOf(r.k.trim()) : 1e6);
  rows = [...rows].sort((a, b) => rank(a) - rank(b));
  const update = (next: VarRow[]) => {
    setStored(next);
    onChange(toVars(next));
  };
  const names = rows.map((r) => r.k.trim());
  const unset = rows.filter((r) => need.has(r.k.trim()) && r.v === "" && !(r.k.trim() in defaults));
  return (
    <div id="f-variables">
      {unset.length > 0 && (
        <div className="text-warn mb-2 text-sm">
          {plural(unset.length, "variable needs", "variables need")} a value: the instructions can't be built without {unset.length === 1 ? "it" : "them"}.
        </div>
      )}
      <div className="grid gap-2">
        {rows.map((r, i) => {
          const name = r.k.trim();
          const from = need.get(name);
          const missing = !!from && r.v === "" && !(name in defaults);
          const dup = name && names.indexOf(name) !== i;
          return (
            <div key={`${r.fixed ? "f" : "c"}${i}`} data-var-row className={cn("grid grid-cols-[minmax(120px,220px)_1fr_32px] items-center gap-x-2 gap-y-0.5", (missing || dup) && "[&_input]:border-err")}>
              {r.fixed ? (
                <span className="truncate">
                  <code className="text-sm">{`{{${name}}}`}</code>
                </span>
              ) : (
                <Input value={r.k} placeholder="name" aria-label="Variable name" onChange={(e) => update(rows.map((x, j) => (j === i ? { ...x, k: e.target.value } : x)))} />
              )}
              <Input
                value={r.v}
                placeholder={name in defaults ? `Default: ${defaults[name]}` : missing ? "Needs a value" : "value"}
                aria-label={`Value of ${name || "variable"}`}
                onChange={(e) => update(rows.map((x, j) => (j === i ? { ...x, v: e.target.value } : x)))}
              />
              {r.fixed ? <span /> : <RemoveVar name={name} onClick={() => update(rows.filter((_, j) => j !== i))} />}
              {(from || name) && <div className="text-muted-foreground col-start-2 col-end-[-1] text-sm">{from ? `Used by ${from.join(", ")}` : "No block uses it"}</div>}
              {dup && <div className="text-err col-span-full text-sm">This name is used twice; the last value wins.</div>}
            </div>
          );
        })}
      </div>
      <AddVar
        onClick={(e) => {
          update([...rows, { k: "", v: "" }]);
          focusLast(e);
        }}
      />
    </div>
  );
}

/** Unsaved-edits state; the ref is read by the leave guard, so clearing it takes effect before a navigation. */
export function useDirty(initial = false) {
  const ref = useRef(initial);
  const [dirty, setDirty] = useState(initial);
  return {
    dirty,
    ref,
    mark() {
      ref.current = true;
      setDirty(true);
    },
    clear() {
      ref.current = false;
      setDirty(false);
    },
  };
}
export type Dirty = ReturnType<typeof useDirty>;

/** Who to record with a change. Signed in, nobody: the server records the user. */
export const author = () => (signedIn() ? null : prefs.get("author")?.trim() || null);

const DELETED: Record<ListKind, string> = {
  instruction: "and took it out of every agent and group that listed it",
  procedure: "and took it out of every agent and group that listed it",
  group: "and listed its blocks in its place wherever it was used, so no agent's instructions change",
  agent: "",
};

/** Saving and deleting one item, with sopc's issues split into this form's fields and everything else. */
export function useItemSave(kind: Kind, id: string, isNew: boolean) {
  const { ws, model, href } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const [errors, setErrors] = useState<Errors>([]);
  const [banner, setBanner] = useState<ReactNode>(null);

  const save = async (data: Record<string, unknown>, note: string | null): Promise<boolean> => {
    const target = isNew ? String(data.id ?? "") : id;
    if (kind !== "settings" && isNew && !ITEM_ID.test(target)) {
      setErrors([localIssue(target ? "Use letters, digits, '-' and '_', starting with a letter or digit." : "Give it a name.", "id")]);
      setBanner(<Banner tone="err">Fix the highlighted fields, then save again.</Banner>);
      return false;
    }
    const body = { ...data, author: author(), note } as never;
    const p = { workspace: ws };
    try {
      let res: { changed: unknown[] };
      if (kind === "settings") res = await call(sdk.putSettings({ path: p, body }));
      else if (isNew) {
        const create = { agent: sdk.configCreateAgent, instruction: sdk.configCreateInstruction, procedure: sdk.configCreateProcedure, group: sdk.configCreateGroup }[kind];
        res = await call(create({ path: p, body }) as Saving);
      } else {
        const put = { agent: sdk.configPutAgent, instruction: sdk.configPutInstruction, procedure: sdk.configPutProcedure, group: sdk.configPutGroup }[kind];
        res = await call(put({ path: { ...p, id }, body }) as Saving);
      }
      setErrors([]);
      setBanner(null);
      toast.success(res.changed.length ? (isNew ? "Created as a draft. Publish to send it to agents." : "Saved as a draft. Publish to send it to agents.") : "Nothing changed.");
      await refresh(qc, ws);
      return true;
    } catch (e) {
      const issues = e instanceof ApiError ? e.issues : [];
      const mine = issues.filter((i) => (i.kind === kind && (kind === "settings" || i.id === target)) || (!i.kind && i.field === "id"));
      const others = issues.filter((i) => !mine.includes(i));
      setErrors(mine);
      setBanner(
        others.length || !issues.length ? (
          <ErrorBanner
            error={others.length ? new ApiError("", 422, { issues: others }) : e}
            lead={mine.length ? "Fix the highlighted fields. This change also causes problems elsewhere:" : "This change can't be saved, because:"}
          />
        ) : (
          <Banner tone="err">Fix the highlighted fields, then save again.</Banner>
        ),
      );
      setTimeout(() => document.querySelector("[data-field] .border-err, .border-err, .text-err")?.scrollIntoView({ block: "center" }), 0);
      return false;
    }
  };

  const remove = async () => {
    if (kind === "settings") return;
    try {
      const del = { agent: sdk.configDeleteAgent, instruction: sdk.configDeleteInstruction, procedure: sdk.configDeleteProcedure, group: sdk.configDeleteGroup }[kind];
      await call(del({ path: { workspace: ws, id }, query: { author: author() } }));
      const what = DELETED[kind];
      toast.success(`Deleted ${model.itemName(kind, id)} in the draft${what ? `, ${what}` : ""}. Publish to make it final.`);
      await refresh(qc, ws);
      go(href(kind));
    } catch (e) {
      setBanner(<ErrorBanner error={e} lead="It can't be deleted yet:" />);
    }
  };

  return { errors, setErrors, banner, setBanner, save, remove };
}

/** Save draft (with a note) and Delete, sticky at the bottom; asks before leaving with unsaved edits. */
export function ActionBar({
  kind,
  isNew,
  dirty,
  onSave,
  onDelete,
  itemId,
}: {
  kind: Kind;
  itemId?: string;
  isNew: boolean;
  dirty: Dirty;
  onSave: (note: string | null, then?: () => void) => void;
  onDelete?: () => void;
}) {
  const [note, setNote] = useState("");
  const blocker = useBlocker({ shouldBlockFn: () => dirty.ref.current, enableBeforeUnload: () => dirty.ref.current, withResolver: true });
  const save = (then?: () => void) => onSave(note.trim() || null, then);
  const unsaved = useUnsaved();
  const { draft } = useWs();
  const unsavedHere = unsaved.find((u) => u.kind === kind)?.label ?? `this ${KIND[kind].one.toLowerCase()}`;
  const inDraft = !isNew && !!itemId && draft.items.some((i) => i.kind === kind && i.id === itemId);
  const otherDrafts = draft.items.filter((i) => !(i.kind === kind && i.id === itemId)).length;
  return (
    <>
      {blocker.status === "blocked" && (
        <Banner tone="warn" className="sticky bottom-[66px] z-10 mt-6 flex flex-wrap items-center gap-2 shadow-md">
          <span className="mr-auto">
            Your edits to {unsavedHere} aren't saved yet.
            {otherDrafts > 0 && ` (${plural(otherDrafts, "other saved draft change stays", "other saved draft changes stay")} either way.)`}
          </span>
          <Button size="sm" onClick={() => save(() => blocker.proceed?.())}>
            Save draft and leave
          </Button>
          <Button size="sm" variant="outline" onClick={() => blocker.proceed?.()}>
            Discard and leave
          </Button>
          <Button size="sm" variant="ghost" onClick={() => blocker.reset?.()}>
            Stay
          </Button>
        </Banner>
      )}
      <div className="bg-background/90 sticky bottom-0 z-10 mt-6 flex flex-wrap items-center gap-2 border-t py-3 backdrop-blur-sm">
        <Button type="button" disabled={!isNew && !dirty.dirty} onClick={() => save()}>
          {isNew ? `Create ${KIND[kind].one.toLowerCase()}` : "Save draft"}
        </Button>
        <Input
          className="max-w-80"
          placeholder="What changed? (optional)"
          aria-label="Note for this change"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && (isNew || dirty.dirty) && save()}
        />
        <span className={cn("text-sm", dirty.dirty ? "text-warn" : "text-muted-foreground")}>
          {dirty.dirty ? "Unsaved edits: not a draft yet" : inDraft ? "Saved as a draft, not published yet" : isNew ? "" : "Saved edits are drafts until you publish."}
        </span>
        <span className="flex-1" />
        {onDelete && (
          <ConfirmButton variant="outline" className="text-err" confirm="Click again to delete" onConfirm={onDelete}>
            Delete
          </ConfirmButton>
        )}
      </div>
    </>
  );
}
