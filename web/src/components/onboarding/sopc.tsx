// Importing an existing sopc project: pick its folder (or read it from the connected GitHub repository), then on the
// import page add each agent's original instructions, read the fidelity check, and apply the files as drafts.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderUp, GitBranch } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import type { AgentFidelity, ImportOut, Proposal } from "@/client/types.gen";
import { call, refresh, sdk } from "@/lib/api";
import { plural } from "@/lib/kinds";
import { pct } from "@/lib/onboarding";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ConfirmButton, ErrorBanner, Note, Panel, Tag } from "../bits";
import { author } from "../form";
import { HrefLink, useGo } from "../href-link";
import { Hint, TipBody } from "../insights/kit";
import { repoQuery } from "../repo";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { Textarea } from "../ui/textarea";

const TEXT = /\.(ya?ml|md|markdown|txt|json)$/i;

/** The picked files as a sopc project: paths relative to the folder holding sopc.yaml, text files only, no build/. */
export async function readProject(list: FileList | File[]): Promise<Record<string, string>> {
  const files = Array.from(list).map((f) => ({ f, path: (f.webkitRelativePath || f.name).replace(/\\/g, "/") }));
  const config = files.map((x) => x.path).filter((p) => p === "sopc.yaml" || p.endsWith("/sopc.yaml")).sort((a, b) => a.length - b.length)[0];
  const root = config ? config.slice(0, -"sopc.yaml".length) : "";
  const out: Record<string, string> = {};
  for (const { f, path } of files) {
    if (!path.startsWith(root) || !TEXT.test(path)) continue;
    const rel = path.slice(root.length);
    if (rel.startsWith("build/")) continue;
    out[rel] = await f.text();
  }
  return out;
}

export function SopcSource() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const repo = useQuery(repoQuery(ws));
  const pick = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<Record<string, string> | null>(null);
  const [busy, setBusy] = useState<"files" | "repo" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const start = async (body: { files?: Record<string, string>; from_repo?: boolean }) => {
    setBusy(body.from_repo ? "repo" : "files");
    setError(null);
    try {
      const created = await call(sdk.importSopcProject({ path: { workspace: ws }, body: { ...body, author: author() } }));
      await qc.invalidateQueries({ queryKey: ["ws", ws, "imports"] });
      go(wsHref(ws, "import", created.id));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  const paths = files ? Object.keys(files).sort() : [];
  return (
    <div className="grid gap-4">
      <Panel title="Your sopc project" className="mb-0">
        <p className="text-muted-foreground mb-3 text-sm">
          Already keep your instructions as a sopc project, or exported one from wherever your agents' instructions live? Bring it in as it is: sopops checks it with sopc, and next you can add each
          agent's original instructions to check nothing was lost. Applying saves exactly these files as drafts. Nothing is published.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={() => pick.current?.click()}>
            <FolderUp /> Choose the project folder
          </Button>
          <input
            ref={pick}
            type="file"
            multiple
            className="hidden"
            {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
            onChange={async (e) => e.target.files && setFiles(await readProject(e.target.files))}
          />
          {repo.data && (
            <Hint focusable={false} tip={<TipBody head={`${repo.data.owner}/${repo.data.repo}`}>Reads the {repo.data.path || "repository root"} folder on {repo.data.base_branch}, as it is now. Your drafts don't change until you apply.</TipBody>}>
              <Button variant="outline" disabled={!!busy} onClick={() => void start({ from_repo: true })}>
                <GitBranch /> {busy === "repo" ? "Reading…" : `Read it from ${repo.data.owner}/${repo.data.repo}`}
              </Button>
            </Hint>
          )}
        </div>
        {files && (
          <div className="mt-3 text-sm">
            {"sopc.yaml" in files ? (
              <Note>
                {plural(paths.length, "file", "files")}: {paths.slice(0, 6).join(", ")}
                {paths.length > 6 ? ` and ${paths.length - 6} more` : ""}.
              </Note>
            ) : (
              <Banner tone="warn" className="mb-0">
                No sopc.yaml in what you picked. Choose the folder that holds it (usually <code>sops/</code>).
              </Banner>
            )}
          </div>
        )}
      </Panel>
      {error ? <ErrorBanner error={error} lead="The import can't start:" /> : null}
      <div>
        <Button disabled={!!busy || !files || !("sopc.yaml" in files)} onClick={() => files && void start({ files })}>
          {busy === "files" ? "Checking…" : "Import the project"}
        </Button>
      </div>
    </div>
  );
}

// --- the import page ---------------------------------------------------------------------------

export function SopcReview({ imp, proposal }: { imp: ImportOut; proposal: Proposal }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [text, setText] = useState("");
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
  const errors = (proposal.issues ?? []).filter((i) => i.severity === "error");
  const fidelity = new Map((proposal.fidelity ?? []).map((f) => [f.agent, f]));
  const originals = proposal.originals ?? {};
  const files = Object.keys(proposal.files ?? {});
  const saveOriginal = (agent: string, value: string) =>
    act("original", async () => {
      await call(sdk.setImportOriginals({ path: { workspace: ws, id: imp.id }, body: { originals: { [agent]: value } } }));
      setEditing(null);
      await reload();
    });
  const apply = () =>
    act("apply", async () => {
      const res = await call(sdk.applyImport({ path: { workspace: ws, id: imp.id }, body: { author: author() } }));
      await refresh(qc, ws);
      toast.success(`Saved as drafts: ${plural(res.changed.length, "file", "files")}.`, { description: res.replaced.length ? `Replaced: ${res.replaced.join(", ")}` : undefined });
      go(wsHref(ws, "changes"));
    });
  const discard = () => act("discard", async () => (await call(sdk.discardImport({ path: { workspace: ws, id: imp.id } })), reload()));
  const counts: [string, number, string][] = [
    ["agents", (proposal.agents ?? []).length, "Agents in the project, each compiled into its full instructions."],
    ["shared instructions", (proposal.instructions ?? []).length, "Blocks of text agents include in their instructions."],
    ["procedures", (proposal.procedures ?? []).length, "SOPs: steps, never rules and warning signs, checked on every session they apply to."],
    ["groups", (proposal.groups ?? []).length, "Named lists of blocks that agents include as one."],
    ["files", files.length, "What applying writes, exactly as they are (build/ is left out)."],
  ];
  return (
    <>
      <Panel title="What's in the project">
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {counts.map(([label, n, tip]) => (
            <Hint key={label} focusable={false} tip={tip}>
              <span className="grid">
                <span className="text-lg font-semibold tabular-nums">{n}</span>
                <span className="text-muted-foreground text-xs">{label}</span>
              </span>
            </Hint>
          ))}
        </div>
      </Panel>
      {errors.length > 0 && (
        <Banner tone="err">
          sopc can't build this project, so it can't be applied. Fix it and import it again:
          <ul>
            {errors.map((i, n) => (
              <li key={n}>
                {i.path ? `${i.path}: ` : ""}
                {i.message}
              </li>
            ))}
          </ul>
        </Banner>
      )}
      <Panel title="Fidelity">
        <p className="text-muted-foreground mb-3 text-sm">
          Add the instructions each agent runs today, and sopops compares them with what the project compiles to: the share of its sentences kept, whether every rule appears word for word, how
          similar the two texts are, and the lines that are missing.
        </p>
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-muted-foreground text-left text-xs">
              <tr>
                <th className="px-3 py-2 font-medium">Agent</th>
                <th className="px-3 py-2 text-right font-medium">Kept</th>
                <th className="px-3 py-2 text-right font-medium">Rules word for word</th>
                <th className="px-3 py-2 text-right font-medium">Similarity</th>
                <th className="px-3 py-2 text-right font-medium">Missing</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y">
              {(proposal.agents ?? []).map((a) => (
                <FidelityLine
                  key={a.id}
                  agent={a.id}
                  f={fidelity.get(a.id)}
                  has={!!originals[a.id]}
                  onEdit={() => {
                    setText(originals[a.id] ?? "");
                    setEditing(a.id);
                  }}
                />
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      {error ? <ErrorBanner error={error} lead="That didn't work:" /> : null}
      <div className="bg-card sticky bottom-0 z-10 -mx-4 mt-6 flex flex-wrap items-center gap-2 border-t px-4 py-3 md:-mx-8 md:px-8">
        <Button onClick={apply} disabled={!!busy || errors.length > 0}>
          {busy === "apply" ? "Applying…" : "Apply as drafts"}
        </Button>
        <Note>Writes the project's {plural(files.length, "file", "files")} as drafts. Review them in Unpublished changes, then publish.</Note>
        <span className="flex-1" />
        <ConfirmButton variant="ghost" confirm="Discard the import?" onConfirm={discard} disabled={!!busy}>
          Discard
        </ConfirmButton>
      </div>
      <Dialog open={!!editing} onOpenChange={(open) => !open && !busy && setEditing(null)}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editing}: the original instructions</DialogTitle>
            <DialogDescription>Paste the full instructions this agent runs today, exactly as its model gets them. Leave it empty to remove it.</DialogDescription>
          </DialogHeader>
          <Textarea aria-label="Original instructions" className="max-h-[50vh] min-h-56 font-mono text-sm" value={text} onChange={(e) => setText(e.target.value)} />
          <DialogFooter>
            <Button variant="outline" disabled={!!busy} onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button disabled={!!busy} onClick={() => editing && void saveOriginal(editing, text)}>
              {busy === "original" ? "Checking…" : "Save and check"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function FidelityLine({ agent, f, has, onEdit }: { agent: string; f?: AgentFidelity; has: boolean; onEdit: () => void }) {
  const [open, setOpen] = useState(false);
  const missing = f?.missing ?? [];
  const changed = f?.changed ?? [];
  const loose = f?.not_verbatim ?? [];
  const rules = f?.rules ?? 0;
  const verbatim = f?.rules_verbatim ?? 0;
  const dash = <span className="text-muted-foreground">—</span>;
  return (
    <>
      <tr>
        <td className="px-3 py-2 font-medium">{agent}</td>
        <td className={cn("px-3 py-2 text-right tabular-nums", f && (f.ok ? "text-ok" : "text-err"))}>
          {f ? (
            <Hint focusable={false} tip={<TipBody head={`${pct(f.score)} of the original kept`}>{f.sentences} sentences in the original; a reworded one counts as kept.</TipBody>}>
              <span>{pct(f.score)}</span>
            </Hint>
          ) : (
            dash
          )}
        </td>
        <td className={cn("px-3 py-2 text-right tabular-nums", f && verbatim < rules && "text-warn")}>
          {f ? (
            <Hint focusable={false} tip={<TipBody head={`${verbatim} of ${rules} rules word for word`}>Steps, never rules and warning signs the full instructions state, found in the original exactly (spacing and case aside).</TipBody>}>
              <span>
                {verbatim} of {rules}
              </span>
            </Hint>
          ) : (
            dash
          )}
        </td>
        <td className="px-3 py-2 text-right tabular-nums">
          {f ? (
            <Hint focusable={false} tip={<TipBody head={`${pct(f.similarity)} similar`}>How alike the two whole texts are, word by word. Lower when text moved, even if nothing was lost.</TipBody>}>
              <span>{pct(f.similarity)}</span>
            </Hint>
          ) : (
            dash
          )}
        </td>
        <td className="px-3 py-2 text-right tabular-nums">
          {f ? (
            <Hint focusable={false} tip={<TipBody head={plural(missing.length, "line missing", "lines missing")}>Sentences of the original the full instructions don't have{changed.length ? `; ${changed.length} more changed` : ""}.</TipBody>}>
              <span>{missing.length}</span>
            </Hint>
          ) : (
            dash
          )}
        </td>
        <td className="px-3 py-2 text-right whitespace-nowrap">
          {missing.length + changed.length + loose.length > 0 && (
            <Button size="xs" variant="ghost" onClick={() => setOpen(!open)}>
              {open ? "Hide" : "Show"}
            </Button>
          )}
          <Button size="xs" variant={has ? "ghost" : "outline"} onClick={onEdit}>
            {has ? "Edit original" : "Add original"}
          </Button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6} className="bg-muted px-3 py-2">
            <ul className="grid gap-1">
              {missing.map((m, i) => (
                <li key={`m${i}`}>
                  <Tag tone="err">missing</Tag> {m}
                </li>
              ))}
              {changed.map((c, i) => (
                <li key={`c${i}`}>
                  <Tag tone="warn">changed</Tag> <s className="text-muted-foreground">{c.original}</s> → {c.now}
                </li>
              ))}
              {loose.map((r, i) => (
                <li key={`r${i}`}>
                  <Tag tone="warn">not word for word</Tag> {r}
                </li>
              ))}
            </ul>
          </td>
        </tr>
      )}
    </>
  );
}

export function SopcIntro({ imp }: { imp: ImportOut }) {
  const { ws } = useWs();
  return (
    <>
      An existing sopc project{imp.repo ? `, read from ${imp.repo}` : ""}. It's applied exactly as its files; to change it, edit the project and{" "}
      <HrefLink href={wsHref(ws, "import")}>import it again</HrefLink>.
    </>
  );
}
