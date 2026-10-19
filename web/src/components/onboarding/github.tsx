// Onboarding with GitHub: connect the repository (the Settings form), then look for the sopc project in its folder.
// A project sopc builds is imported; without one, the coding agent proposes it in a pull request; one sopc can't build
// is imported to fix, or rewritten by the coding agent.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch, RefreshCw } from "lucide-react";
import { type ReactNode, useState } from "react";
import type { RepoOut, RepoProjectOut } from "@/lib/api";
import { call, sdk } from "@/lib/api";
import { plural } from "@/lib/kinds";
import { useRepoStream } from "@/lib/live";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ErrorBanner, Note } from "../bits";
import { author } from "../form";
import { useGo } from "../href-link";
import { ConnectForm, RepoError, RepoLine, folderOf, repoProjectQuery, repoQuery } from "../repo";
import { Button } from "../ui/button";

/** The connection, with a Change link, or the connect form (a folder without a project is fine here). */
export function RepoConnect() {
  const { ws } = useWs();
  useRepoStream(ws);
  const repo = useQuery(repoQuery(ws));
  const [editing, setEditing] = useState(false);
  if (repo.isPending) return <Note>Loading…</Note>;
  if (repo.isError) return <RepoError error={repo.error} />;
  if (!repo.data || editing)
    return <ConnectForm current={repo.data ?? null} requireProject={false} onDone={() => setEditing(false)} onCancel={() => setEditing(false)} />;
  return (
    <RepoLine r={repo.data}>
      <Button variant="link" size="xs" className="h-auto px-0" onClick={() => setEditing(true)}>
        Change
      </Button>
    </RepoLine>
  );
}

/** What the connected folder holds, and the way forward for each case. `onAgent`: the coding agent should rewrite a
 * project sopc can't build. */
export function ProjectFinding({ repo, mode, useAgent, onAgent }: { repo: RepoOut; mode: "hosted" | "repo"; useAgent: boolean; onAgent: () => void }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const project = useQuery(repoProjectQuery(ws));
  const folder = folderOf(repo.path);
  const where = (
    <>
      <code>{folder}</code> on <code>{repo.base_branch}</code>
    </>
  );
  const recheck = (
    <Button variant="ghost" size="xs" disabled={project.isFetching} onClick={() => void qc.invalidateQueries({ queryKey: repoProjectQuery(ws).queryKey })}>
      <RefreshCw className={project.isFetching ? "animate-spin" : undefined} /> Check again
    </Button>
  );
  if (project.isPending) return <Note>Looking for a sopc project at {where}…</Note>;
  if (project.isError)
    return (
      <div className="grid gap-2">
        <RepoError error={project.error} />
        <div>{recheck}</div>
      </div>
    );
  const p = project.data;
  if (p.state === "valid")
    return (
      <Banner tone="ok" className="mb-0">
        <div className="grid gap-2">
          <div>
            <b>Found a sopc project at {where}</b>: {plural(p.agents.length, "agent", "agents")} ({p.agents.join(", ")}), {plural(p.files, "file", "files")}.
          </div>
          <div>Import it as it is. sopops checks it with sopc, and you can compare each agent with its original instructions. Nothing is published.</div>
          <div className="flex flex-wrap items-center gap-2">
            <ImportFromRepo label="Import it" />
            {recheck}
          </div>
        </div>
      </Banner>
    );
  if (p.state === "invalid") return <Invalid project={p} where={where} folder={folder} mode={mode} useAgent={useAgent} onAgent={onAgent} recheck={recheck} />;
  return (
    <Banner className="mb-0">
      <div className="grid gap-1.5">
        <div>
          <b>No sopc project at {where} yet.</b>
        </div>
        {mode === "repo" ? (
          <div>
            Your coding agent will propose a pull request adding <code>{folder}</code> to {p.repo}. Nothing merges without you. When you merge it, sopops pulls it into drafts.
          </div>
        ) : (
          <div>Fixes arrive as pull requests once it's there. Choose In my repo too to have your agent propose it.</div>
        )}
        <div>{recheck}</div>
      </div>
    </Banner>
  );
}

function Invalid({
  project,
  where,
  folder,
  mode,
  useAgent,
  onAgent,
  recheck,
}: {
  project: RepoProjectOut;
  where: ReactNode;
  folder: string;
  mode: "hosted" | "repo";
  useAgent: boolean;
  onAgent: () => void;
  recheck: ReactNode;
}) {
  const errors = project.issues.filter((i) => i.severity === "error");
  return (
    <Banner tone="warn" className="mb-0">
      <div className="grid gap-2">
        <div>
          <b>{where} has a sopc project, but sopc can't build it:</b>
        </div>
        <ul className="grid gap-0.5 pl-5 text-[13px] [list-style:disc]">
          {errors.slice(0, 8).map((i, n) => (
            <li key={n}>
              {i.path && <code className="text-[12px]">{i.path}</code>} {i.text || i.message}
            </li>
          ))}
          {errors.length > 8 && <li>and {errors.length - 8} more</li>}
        </ul>
        {mode === "repo" && useAgent ? (
          <div>
            Your coding agent will propose a pull request replacing <code>{folder}</code>. Nothing merges without you.
          </div>
        ) : (
          <div>Import it anyway to see what to fix, or let your coding agent rewrite it.</div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <ImportFromRepo label="Import anyway to fix" variant="outline" />
          {mode === "repo" && !useAgent && <Button onClick={onAgent}>Use Claude Code or Codex</Button>}
          {recheck}
        </div>
      </div>
    </Banner>
  );
}

/** Import the connected folder (`POST …/imports/sopc` with `from_repo`), then open the import. */
function ImportFromRepo({ label, variant }: { label: string; variant?: "outline" }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const created = await call(sdk.importSopcProject({ path: { workspace: ws }, body: { from_repo: true, author: author() } }));
      await qc.invalidateQueries({ queryKey: ["ws", ws, "imports"] });
      go(wsHref(ws, "import", created.id));
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };
  return (
    <>
      <Button variant={variant} disabled={busy} onClick={() => void start()}>
        <GitBranch /> {busy ? "Reading…" : label}
      </Button>
      {error ? (
        <div className="basis-full">
          <ErrorBanner error={error} lead="The import can't start:" />
        </div>
      ) : null}
    </>
  );
}
