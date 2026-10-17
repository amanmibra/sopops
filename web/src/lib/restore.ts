// "Restore version N" on a history page opens the item's form with that version's fields, unsaved.
import type { Kind } from "./kinds";

type Pending = { kind: Kind; id: string; fields: Record<string, unknown>; label: string; taken?: number };
let pending: Pending | null = null;

const SKIP = ["id", "file", "version", "updated_at", "updated_by", "format", "deleted", "used_by", "legacy"];

export function setRestore(kind: Kind, id: string, old: Record<string, unknown>, label: string) {
  const fields = Object.fromEntries(Object.entries(old).filter(([k]) => !SKIP.includes(k)));
  pending = { kind, id, fields: structuredClone(fields), label };
}

/** The fields to open the form with, once (a second call right after, as React's dev double-render makes, gets them too). */
export function takeRestore(kind: Kind, id: string): Pending | null {
  if (!pending || pending.kind !== kind || pending.id !== id) return null;
  if (pending.taken && Date.now() - pending.taken > 1000) {
    pending = null;
    return null;
  }
  pending.taken ??= Date.now();
  return pending;
}
