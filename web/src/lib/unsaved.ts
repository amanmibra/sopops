// Which open forms have edits that aren't saved as drafts yet, so the publish dialog and the leave guard can name them.
import { useEffect, useSyncExternalStore } from "react";
import type { Kind } from "./kinds";

export type Unsaved = { kind: Kind; id: string; label: string };

let entries: Unsaved[] = [];
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

/** While `dirty`, list this form (e.g. "Agent tonys-pizza") as having unsaved edits. */
export function useUnsavedRegistration(entry: Unsaved, dirty: boolean) {
  const { kind, id, label } = entry;
  useEffect(() => {
    if (!dirty) return;
    const mine: Unsaved = { kind, id, label };
    entries = [...entries, mine];
    emit();
    return () => {
      entries = entries.filter((e) => e !== mine);
      emit();
    };
  }, [kind, id, label, dirty]);
}

export function useUnsaved(): Unsaved[] {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => entries,
  );
}
