// Imports: queries, and the onboarding token, which the server shows only once and is kept in memory here.
import { queryOptions } from "@tanstack/react-query";
import type { AgentImportOut, ImportOut } from "@/client/types.gen";
import { call, sdk } from "./api";

export type * from "@/client/types.gen";

const path = (workspace: string, id: string) => ({ path: { workspace, id } });

export const TERMINAL: ImportOut["status"][] = ["applied", "discarded"];

export const iq = {
  capabilities: () => queryOptions({ queryKey: ["onboarding", "capabilities"], queryFn: () => call(sdk.onboardingCapabilities()), staleTime: 60_000 }),
  list: (ws: string) => queryOptions({ queryKey: ["ws", ws, "imports"], queryFn: () => call(sdk.listImports({ path: { workspace: ws } })) }),
  one: (ws: string, id: string) =>
    queryOptions({
      queryKey: ["ws", ws, "import", id],
      queryFn: () => call(sdk.getImport(path(ws, id))),
      // Poll while something can still change: an extraction running here, or a coding agent pushing.
      refetchInterval: (q) => {
        const d = q.state.data;
        if (d?.pr_state === "open") return 30_000; // the server checks the onboarding pull request at most once a minute
        if (!d || TERMINAL.includes(d.status) || d.status === "failed") return false;
        return d.status === "running" || d.status === "waiting" ? 1000 : d.token?.active ? 3000 : false;
      },
    }),
  events: (ws: string, id: string, live: boolean) =>
    queryOptions({ queryKey: ["ws", ws, "import", id, "events"], queryFn: () => call(sdk.importEvents(path(ws, id))), refetchInterval: live ? 1000 : false }),
};

// The token and kickoff text from creating an agent import: shown once, so only in this tab's memory.
const kickoffs = new Map<string, AgentImportOut>();
export const rememberKickoff = (k: AgentImportOut) => kickoffs.set(k.import.id, k);
export const kickoffFor = (id: string) => kickoffs.get(id);

/** Files dropped or picked: one agent per .txt or .md file, named after the file. */
export async function readPromptFiles(files: FileList | File[]): Promise<{ name: string; prompt: string }[]> {
  const out = [];
  for (const f of Array.from(files)) {
    if (!/\.(txt|md|markdown)$/i.test(f.name)) continue;
    out.push({ name: f.name.replace(/\.(txt|md|markdown)$/i, ""), prompt: await f.text() });
  }
  return out;
}

export const pct = (x: number) => `${Math.round(x * 100)}%`;
