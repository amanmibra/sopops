// An ordered stack of block cards (drag, arrows, open, remove, read in place) and a library to add from.
import { DndContext, type DragEndEvent, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors } from "@dnd-kit/core";
import { SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, GripVertical, X } from "lucide-react";
import { useState } from "react";
import { call, issuesOf, refresh, sdk, type GroupConfig, type InstructionConfig, type ProcedureConfig } from "@/lib/api";
import { BLOCK_KINDS, KIND, plural, slug } from "@/lib/kinds";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { KindBadge } from "./bits";
import { type Errors, ErrNotes, author, errsFor } from "./form";
import { HrefLink } from "./href-link";
import { Button } from "./ui/button";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "./ui/command";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";

function BlockPreview({ id }: { id: string }) {
  const { model, href } = useWs();
  const kind = model.blockKind(id);
  if (!kind) return <p className="text-muted-foreground text-[13px]">Nothing has this name. Remove it, or create it.</p>;
  const it = model.find(kind, id);
  if (kind === "instruction") return <pre className="plain max-h-80 overflow-auto">{(it as InstructionConfig).text}</pre>;
  if (kind === "procedure") return <pre className="plain max-h-80 overflow-auto">{model.readable("procedure", it as never).join("\n")}</pre>;
  return (
    <ol className="grid list-decimal gap-1 pl-5">
      {(it as GroupConfig).blocks.map((b) => (
        <li key={b}>
          <KindBadge kind={model.blockKind(b)} /> <HrefLink href={href(model.blockKind(b) ?? "instruction", b)}>{model.blockName(b)}</HrefLink>
        </li>
      ))}
    </ol>
  );
}

function Card({
  id,
  index,
  count,
  errors,
  field,
  onMove,
  onRemove,
}: {
  id: string;
  index: number;
  count: number;
  errors: Errors;
  field: string;
  onMove: (to: number) => void;
  onRemove: () => void;
}) {
  const { model, href } = useWs();
  const [open, setOpen] = useState(false);
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({ id });
  const kind = model.blockKind(id);
  const it = kind ? model.find(kind, id) : undefined;
  const name = model.blockName(id);
  let sub = "";
  if (kind === "group") sub = plural((it as GroupConfig).blocks.length, "block", "blocks");
  else if (kind === "procedure") sub = plural((it as ProcedureConfig).steps.length, "step", "steps");
  else if (kind === "instruction") {
    const words = (it as InstructionConfig).text.split(/\s+/);
    sub = words.slice(0, 12).join(" ") + (words.length > 12 ? "…" : "");
  }
  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn("bg-card rounded-lg border", errsFor(errors, field, index).length && "border-err", isDragging && "relative z-10 opacity-80 shadow-lg")}
    >
      <div className="flex min-w-0 items-center gap-2 py-1 pr-1.5 pl-1">
        <button
          type="button"
          ref={setActivatorNodeRef}
          {...attributes}
          {...listeners}
          className="text-muted-foreground cursor-grab touch-none rounded p-1 hover:bg-muted active:cursor-grabbing"
          aria-label={`Drag ${name} to reorder`}
        >
          <GripVertical className="size-4" />
        </button>
        <span className="text-muted-foreground min-w-3.5 text-right text-xs tabular-nums">{index + 1}</span>
        <KindBadge kind={kind} />
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className="group flex min-w-0 flex-1 cursor-pointer items-baseline gap-1 rounded-md px-0.5 py-1 text-left"
        >
          <b className="whitespace-nowrap group-hover:underline">{kind ? name : id}</b>
          <span className="text-muted-foreground ml-1.5 min-w-0 truncate text-[13px]">{sub}</span>
        </button>
        <span className="flex shrink-0 items-center">
          {kind && (
            <Button variant="ghost" size="sm" asChild>
              <HrefLink href={href(kind, id)} className="text-muted-foreground hover:text-foreground hover:no-underline" title={`Open ${name} to edit it`}>
                Open
              </HrefLink>
            </Button>
          )}
          <Button variant="ghost" size="icon-sm" type="button" className="text-muted-foreground" disabled={index === 0} aria-label={`Move ${name} up`} onClick={() => onMove(index - 1)}>
            <ArrowUp />
          </Button>
          <Button variant="ghost" size="icon-sm" type="button" className="text-muted-foreground" disabled={index === count - 1} aria-label={`Move ${name} down`} onClick={() => onMove(index + 1)}>
            <ArrowDown />
          </Button>
          <Button variant="ghost" size="icon-sm" type="button" className="text-muted-foreground" aria-label={`Remove ${name}`} title="Remove" onClick={onRemove}>
            <X />
          </Button>
        </span>
      </div>
      <div className="pr-3 pl-16 [&>div]:mt-0 [&>div]:pb-2">
        <ErrNotes errors={errors} field={field} index={index} />
      </div>
      {open && (
        <div className="pr-3 pb-3 pl-16">
          <BlockPreview id={id} />
        </div>
      )}
    </li>
  );
}

function Library({ value, owner, onPick, onClose, onNew }: { value: string[]; owner?: string; onPick: (id: string) => void; onClose: () => void; onNew: (k: "instruction" | "procedure") => void }) {
  const { model, cfg } = useWs();
  const inList = new Map(model.expand(value).map((x) => [x.id, x.via]));
  value.forEach((b) => inList.set(b, null));
  const lists = { group: cfg.groups, instruction: cfg.instructions, procedure: cfg.procedures };
  const any = BLOCK_KINDS.some((k) => lists[k].some((x) => x.id !== owner));
  return (
    <div className="bg-card mt-2 rounded-xl border border-input p-2 shadow-lg" role="dialog" aria-label="Add a block">
      <Command
        className="bg-transparent"
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
      >
        <div className="flex items-center gap-2 [&>div]:flex-1">
          <CommandInput autoFocus placeholder="Search shared instructions, procedures and groups" aria-label="Search blocks" />
          <Button variant="ghost" size="sm" type="button" onClick={onClose}>
            Close
          </Button>
        </div>
        <CommandList className="max-h-80">
          <CommandEmpty>{any ? "Nothing matches." : "Nothing to add yet. Create a block below."}</CommandEmpty>
          {BLOCK_KINDS.map((k) => {
            const items = lists[k].filter((x) => x.id !== owner);
            if (!items.length) return null;
            return (
              <CommandGroup key={k} heading={KIND[k].many}>
                {items.map((x) => {
                  const there = inList.has(x.id);
                  const via = inList.get(x.id);
                  const why = there ? (via ? `in ${via}` : "already listed") : `${plural(new Set(x.used_by.map((u) => u.agent)).size, "agent", "agents")}`;
                  const text = k === "instruction" ? (x as InstructionConfig).text.slice(0, 400) : "";
                  return (
                    <CommandItem key={x.id} value={`${x.id} ${model.itemName(k, x.id)} ${text}`} disabled={there} onSelect={() => onPick(x.id)}>
                      <KindBadge kind={k} />
                      <span className="min-w-0 flex-1 truncate">{model.itemName(k, x.id)}</span>
                      <span className="text-muted-foreground text-[13px]">{why}</span>
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            );
          })}
        </CommandList>
      </Command>
      <div className="mt-2 flex flex-wrap gap-2 border-t pt-2.5">
        <Button variant="outline" size="sm" type="button" onClick={() => onNew("instruction")}>
          + New shared instruction
        </Button>
        <Button variant="outline" size="sm" type="button" onClick={() => onNew("procedure")}>
          + New procedure
        </Button>
      </div>
    </div>
  );
}

function QuickCreate({ kind, onCreated, onCancel }: { kind: "instruction" | "procedure"; onCreated: (id: string) => void; onCancel: () => void }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const isProc = kind === "procedure";
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const create = async () => {
    const id = isProc ? slug(name) : name.trim();
    setBusy(true);
    try {
      if (isProc) {
        const steps = text.split("\n").map((t) => t.trim()).filter(Boolean).map((t) => ({ text: t }));
        await call(sdk.configCreateProcedure({ path: { workspace: ws }, body: { id, name: name.trim(), goal: goal.trim(), steps, author: author() } as never }));
      } else await call(sdk.configCreateInstruction({ path: { workspace: ws }, body: { id, text, author: author() } }));
      await refresh(qc, ws);
      onCreated(id);
    } catch (e) {
      const issues = issuesOf(e);
      setError(issues.length ? issues.map((i) => i.text || i.message).join(" ") : (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="bg-card mt-2 rounded-xl border px-4 py-3.5">
      <h3 className="mb-1 font-semibold">{isProc ? "New procedure" : "New shared instruction"}</h3>
      <p className="text-muted-foreground mb-2 text-[13px]">
        {isProc ? "Saved as a draft and added to this list. Open it afterwards to add never-do's, warning signs and tools." : "Saved as a draft and added to this list."}
      </p>
      <div className="grid gap-2.5">
        <label>
          <span className="mb-0.5 block font-semibold">Name</span>
          <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder={isProc ? "Takeout" : "hours"} />
        </label>
        {isProc && (
          <label>
            <span className="mb-0.5 block font-semibold">Goal</span>
            <Input value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="The order is placed with a pickup time." />
          </label>
        )}
        <label>
          <span className="mb-0.5 block font-semibold">{isProc ? "Steps" : "Text"}</span>
          <Textarea value={text} onChange={(e) => setText(e.target.value)} placeholder={isProc ? "One step per line" : "What the agents are told"} />
        </label>
      </div>
      {error && <div className="text-err mt-1.5 text-[13px]">{error}</div>}
      <div className="mt-2.5 flex gap-2">
        <Button size="sm" type="button" disabled={busy} onClick={create}>
          Create and add
        </Button>
        <Button size="sm" variant="ghost" type="button" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

export function BlockStack({ field = "blocks", value, onChange, errors, owner }: { field?: string; value: string[]; onChange: (v: string[]) => void; errors: Errors; owner?: string }) {
  const [mode, setMode] = useState<"idle" | "library" | "instruction" | "procedure">("idle");
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }));
  const move = (from: number, to: number) => {
    if (to < 0 || to >= value.length || from === to) return;
    onChange(arrayMove(value, from, to));
  };
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (over && active.id !== over.id) move(value.indexOf(String(active.id)), value.indexOf(String(over.id)));
  };
  return (
    <div id={`f-${field}`}>
      {value.length ? (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext items={value} strategy={verticalListSortingStrategy}>
            <ol className="grid grid-cols-[minmax(0,1fr)] gap-1.5" aria-label="Blocks, in prompt order">
              {value.map((id, i) => (
                <Card key={id} id={id} index={i} count={value.length} errors={errors} field={field} onMove={(to) => move(i, to)} onRemove={() => onChange(value.filter((_, j) => j !== i))} />
              ))}
            </ol>
          </SortableContext>
        </DndContext>
      ) : (
        <p className="text-muted-foreground text-[13px]">No blocks yet.</p>
      )}
      {mode === "library" ? (
        <Library
          value={value}
          owner={owner}
          onPick={(id) => {
            onChange([...value, id]);
            setMode("idle");
          }}
          onClose={() => setMode("idle")}
          onNew={(k) => setMode(k)}
        />
      ) : mode === "idle" ? (
        <Button variant="outline" size="sm" type="button" className="mt-2" onClick={() => setMode("library")}>
          + Add block
        </Button>
      ) : (
        <QuickCreate
          kind={mode}
          onCancel={() => setMode("idle")}
          onCreated={(id) => {
            onChange([...value, id]);
            setMode("idle");
          }}
        />
      )}
    </div>
  );
}
