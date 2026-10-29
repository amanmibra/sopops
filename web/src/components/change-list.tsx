// What changed in an item, field by field, in plain words.
import type { ReactNode } from "react";
import { diffCount, diffLines, diffRows, splitLines } from "@/lib/diff";
import { type Kind, deliveryName, platformName } from "@/lib/kinds";
import { type Step, stepWithOptions } from "@/lib/model";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { DiffView } from "./diff-view";

const FIELD_LABELS: Record<Kind, [string, string][]> = {
  agent: [["platform", "Platform"], ["platform_id", "Platform id"], ["context", "About this agent"], ["blocks", "Blocks"], ["variables", "Variables"]],
  instruction: [["text", "Text"]],
  procedure: [["name", "Name"], ["goal", "Goal"], ["when", "When it applies"], ["guidance", "Guidance"], ["steps", "Steps"], ["never", "Never"], ["warning_signs", "Warning signs"], ["delivery", "Delivery"]],
  group: [["blocks", "Blocks"]],
  settings: [["variables", "Default variables"], ["procedures_heading", "Procedures heading"]],
};
const TEXT_FIELDS = ["context", "text", "guidance"];
const STEP_FIELDS = ["steps", "never", "warning_signs"];

type Bit = { t: string; c: "add" | "del" | "mod" | "" };
type Change = { label: string; summary: string | Bit[]; values?: [string, string]; detail?: () => ReactNode };

function scalarText(field: string, v: unknown): string {
  if (field === "platform") return platformName(String(v));
  if (field === "delivery") return deliveryName(String(v));
  return v === "" ? "(empty)" : String(v);
}

export function useFieldChanges() {
  const { model } = useWs();
  const name = model.blockName;

  // "+ reservations", "− pizza-context", "moved closing to the end": a blocks list change in words.
  const listChanges = (a: string[], b: string[]): Bit[] => {
    const bits: Bit[] = [
      ...b.filter((x) => !a.includes(x)).map((x): Bit => ({ t: `+ ${name(x)}`, c: "add" })),
      ...a.filter((x) => !b.includes(x)).map((x): Bit => ({ t: `− ${name(x)}`, c: "del" })),
    ];
    const keptA = a.filter((x) => b.includes(x)),
      keptB = b.filter((x) => a.includes(x));
    // Blocks outside the longest run kept in order are the ones that moved.
    const stay = new Set(diffLines(keptA, keptB).filter((o) => o.op === " ").map((o) => o.text));
    for (const x of keptB.filter((y) => !stay.has(y))) {
      const i = b.indexOf(x);
      const where = i === b.length - 1 ? "to the end" : i === 0 ? "to the top" : `after ${name(b[i - 1])}`;
      bits.push({ t: `moved ${name(x)} ${where}`, c: "mod" });
    }
    return bits;
  };

  return (kind: Kind, before: Record<string, unknown>, after: Record<string, unknown>): Change[] => {
    const out: Change[] = [];
    for (const [field, label] of FIELD_LABELS[kind]) {
      const a = before[field] as never,
        b = after[field] as never;
      if (JSON.stringify(a) === JSON.stringify(b)) continue;
      if (TEXT_FIELDS.includes(field)) {
        const c = diffCount(diffLines(splitLines(a), splitLines(b)));
        out.push({
          label,
          summary: !a ? "added" : !b ? "removed" : `edited, ${c.add} line${c.add === 1 ? "" : "s"} in, ${c.del} out`,
          detail: () => <DiffView before={a} after={b} toggle={false} context={2} />,
        });
      } else if (STEP_FIELDS.includes(field)) {
        const la = (a as Step[]).map(stepWithOptions),
          lb = (b as Step[]).map(stepWithOptions);
        let added = 0,
          removed = 0,
          edited = 0;
        for (const r of diffRows(diffLines(la, lb)))
          if (r.block) {
            const p = Math.min(r.del.length, r.add.length);
            edited += p;
            added += r.add.length - p;
            removed += r.del.length - p;
          }
        const parts = [added && `${added} added`, edited && `${edited} edited`, removed && `${removed} removed`].filter(Boolean);
        out.push({ label, summary: parts.length ? parts.join(", ") : "reordered", detail: () => <DiffView before={la} after={lb} toggle={false} context={99} /> });
      } else if (field === "blocks") {
        const bits = listChanges(a, b);
        out.push({
          label,
          summary: bits.length ? bits : [{ t: "order changed", c: "" }],
          detail: () => <DiffView before={(a as string[]).map(name)} after={(b as string[]).map(name)} toggle={false} context={99} />,
        });
      } else if (field === "variables") {
        const va = a as Record<string, string>,
          vb = b as Record<string, string>;
        const keys = [...new Set([...Object.keys(va), ...Object.keys(vb)])];
        const bits: Bit[] = [];
        for (const k of keys) {
          if (!(k in va)) bits.push({ t: `+ ${k}`, c: "add" });
          else if (!(k in vb)) bits.push({ t: `− ${k}`, c: "del" });
          else if (va[k] !== vb[k]) bits.push({ t: `${k} changed`, c: "mod" });
        }
        out.push({
          label,
          summary: bits.length ? bits : [{ t: "order changed", c: "" }],
          detail: () => (
            <table className="mt-1">
              <tbody>
                {keys
                  .filter((k) => va[k] !== vb[k])
                  .map((k) => (
                    <tr key={k} className="[&>td]:py-0.5 [&>td]:pr-2 [&>td]:align-top">
                      <td>{k}</td>
                      <td>{k in va ? <span className="was">{va[k]}</span> : <span className="text-muted-foreground text-sm">not set</span>}</td>
                      <td className="text-muted-foreground">→</td>
                      <td>{k in vb ? <span className="now">{vb[k]}</span> : <span className="text-muted-foreground text-sm">removed</span>}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          ),
        });
      } else out.push({ label, summary: "changed", values: [scalarText(field, a), scalarText(field, b)] });
    }
    return out;
  };
}

const BIT = { add: "text-ok", del: "text-err", mod: "text-warn", "": "" };

export function ChangeList({ kind, before, after }: { kind: Kind; before: Record<string, unknown>; after: Record<string, unknown> }) {
  const changes = useFieldChanges()(kind, before, after);
  if (!changes.length) return <p className="text-muted-foreground text-sm">No field changed (the saved text was only reformatted).</p>;
  return (
    <ul className="grid gap-3">
      {changes.map((c) => (
        <li key={c.label}>
          <div className="flex flex-wrap items-baseline gap-2">
            <b>{c.label}</b>
            {Array.isArray(c.summary) ? (
              c.summary.map((b, i) => (
                <span key={i} className={cn("rounded-md bg-muted px-2 text-sm", BIT[b.c])}>
                  {b.t}
                </span>
              ))
            ) : (
              <span className="text-muted-foreground">{c.summary}</span>
            )}
          </div>
          {c.values && (
            <div className="mt-1 flex flex-wrap items-baseline gap-2">
              <span className="was">{c.values[0]}</span>
              <span className="text-muted-foreground" aria-label="becomes">
                →
              </span>
              <span className="now">{c.values[1]}</span>
            </div>
          )}
          {c.detail?.()}
        </li>
      ))}
    </ul>
  );
}
