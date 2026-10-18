// Settings: the workspace's GitHub repository. Connect it (the token is write-only), pull its sopc project into drafts,
// disconnect. Suggestions open pull requests on it. Onboarding reuses the connect form and the connection line.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, GitBranch, Loader2 } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import { ApiError, type PullOut, type RepoOut, type RepoProjectOut, call, refresh, sdk } from "@/lib/api";
import { when } from "@/lib/kinds";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ConfirmButton, Note, Panel } from "./bits";
import { author } from "./form";
import { HrefLink } from "./href-link";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";

/** The workspace's repository, or null when none is connected. */
export const repoQuery = (ws: string) => ({
  queryKey: ["ws", ws, "repo"],
  queryFn: async (): Promise<RepoOut | null> => (await call(sdk.getRepo({ path: { workspace: ws } }))) ?? null,
});

/** What the connected folder holds at the branch's head: missing, invalid (sopc's issues) or valid. */
export const repoProjectQuery = (ws: string) => ({
  queryKey: ["ws", ws, "repo", "project"],
  queryFn: (): Promise<RepoProjectOut> => call(sdk.inspectRepoProject({ path: { workspace: ws } })),
  staleTime: 30_000,
});

/** The project's folder as people read it. */
export const folderOf = (path: string | null | undefined) => (path ? `${path}/` : "the repository root");

const LEAD: Record<number, string> = {
  400: "GitHub didn't accept this. Fix it and try again:",
  409: "Conflict:",
  429: "Rate limited:",
  502: "GitHub couldn't be reached:",
  503: "The server isn't set up for this:",
};

export function RepoError({ error }: { error: unknown }) {
  const status = error instanceof ApiError ? error.status : 0;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <Banner tone={status === 429 || status === 409 ? "warn" : "err"}>
      {LEAD[status] && <b>{LEAD[status]} </b>}
      {msg}
      {status === 503 && " Set SOPOPS_SECRET_KEY where sopops runs, so the token can be stored encrypted."}
    </Banner>
  );
}

/** Field errors from a 422: `detail[].loc` ends with the field's name. */
function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || error.status !== 422) return {};
  const detail = (error.data as { detail?: { loc?: (string | number)[]; msg?: string }[] } | null)?.detail;
  const out: Record<string, string> = {};
  if (Array.isArray(detail)) for (const d of detail) if (d.loc?.length) out[String(d.loc[d.loc.length - 1])] = d.msg ?? "Not valid.";
  return out;
}

function Field({ id, label, hint, error, children }: { id: string; label: string; hint?: ReactNode; error?: string; children: ReactNode }) {
  return (
    <div className="grid content-start gap-1">
      <Label htmlFor={id} className="text-[13px]">
        {label}
      </Label>
      {children}
      {error ? (
        <span id={`${id}-err`} className="text-err text-xs">
          {error}
        </span>
      ) : (
        hint && <span className="text-muted-foreground text-xs">{hint}</span>
      )}
    </div>
  );
}

/** Connect the repository, or change the connection. `requireProject: false` (onboarding) connects before the folder
 * holds a sopc project. */
export function ConnectForm({
  current,
  onDone,
  onCancel,
  requireProject = true,
}: {
  current: RepoOut | null;
  onDone: (r: RepoOut) => void;
  onCancel?: () => void;
  requireProject?: boolean;
}) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [v, setV] = useState({ owner: current?.owner ?? "", repo: current?.repo ?? "", token: "", base_branch: current?.base_branch ?? "main", path: current?.path ?? "sops" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const fields = fieldErrors(error);
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement>) => setV({ ...v, [k]: e.target.value });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await call(
        sdk.connectRepo({
          path: { workspace: ws },
          body: { ...v, owner: v.owner.trim(), repo: v.repo.trim(), token: v.token.trim() || null, path: v.path.trim(), require_project: requireProject },
        }),
      );
      toast.success(`Connected ${r.owner}/${r.repo}.`);
      await qc.invalidateQueries({ queryKey: ["ws", ws, "repo"] });
      onDone(r);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  const input = (k: keyof typeof v, extra: React.ComponentProps<typeof Input> = {}) => (
    <Input id={`repo-${k}`} value={v[k]} onChange={set(k)} aria-invalid={!!fields[k]} aria-describedby={fields[k] ? `repo-${k}-err` : undefined} className="h-9" {...extra} />
  );
  return (
    <form onSubmit={(e) => void submit(e)} className="grid gap-3">
      {error != null && !Object.keys(fields).length && <RepoError error={error} />}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field id="repo-owner" label="Owner" hint="The user or organization" error={fields.owner}>
          {input("owner", { placeholder: "acme", required: true, autoComplete: "off" })}
        </Field>
        <Field id="repo-repo" label="Repository" error={fields.repo}>
          {input("repo", { placeholder: "voice-agents", required: true, autoComplete: "off" })}
        </Field>
        <Field id="repo-base_branch" label="Base branch" hint="Pulled from, and pull requests target it" error={fields.base_branch}>
          {input("base_branch", { required: true })}
        </Field>
        <Field
          id="repo-path"
          label="Folder"
          hint={requireProject ? "The sopc project (with sopc.yaml); empty for the root" : "Where the sopc project is, or will go; empty for the root"}
          error={fields.path}
        >
          {input("path")}
        </Field>
      </div>
      <Field
        id="repo-token"
        label="Access token"
        error={fields.token}
        hint={current ? `Stored, ending in ${current.token_hint}. Leave empty to keep it.` : "Stored encrypted and never shown again."}
      >
        {input("token", { type: "password", autoComplete: "new-password", placeholder: current ? `••••${current.token_hint.replace(/^…/, "")}` : "github_pat_…", required: !current })}
      </Field>
      <div className="bg-muted rounded-md px-3 py-2 text-[13px]">
        Use a <b>fine-grained personal access token</b> limited to this repository, with:
        <ul className="mt-1 list-disc pl-5">
          <li>Contents: read and write</li>
          <li>Pull requests: read and write</li>
          <li>Metadata: read (GitHub adds it)</li>
        </ul>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {busy && <Loader2 className="animate-spin" />} {current ? "Save" : "Connect"}
        </Button>
        {current && onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}

/** The connection on one line: the folder on GitHub, the branch, the token's last characters, and an optional action. */
export function RepoLine({ r, children }: { r: RepoOut; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <GitBranch className="text-muted-foreground size-4" />
      <a href={r.url} target="_blank" rel="noreferrer" className="text-primary inline-flex items-center gap-1 font-semibold break-all hover:underline">
        {r.owner}/{r.repo}
        {r.path ? `/${r.path}` : ""} <ExternalLink className="size-3.5 shrink-0" />
      </a>
      <span className="text-muted-foreground text-[13px]">
        on <code className="text-[12.5px]">{r.base_branch}</code> · token ••••{r.token_hint.replace(/^…/, "")}
      </span>
      {children}
    </div>
  );
}

export function RepoCard() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const res = useQuery(repoQuery(ws));
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<null | "pull" | "disconnect">(null);
  const [error, setError] = useState<unknown>(null);
  const [pulled, setPulled] = useState<PullOut | null>(null);
  const r = res.data;

  const pull = async () => {
    setBusy("pull");
    setError(null);
    try {
      const out = await call(sdk.pullRepo({ path: { workspace: ws }, query: { author: author() } }));
      setPulled(out);
      await refresh(qc, ws);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  const disconnect = async () => {
    setBusy("disconnect");
    setError(null);
    try {
      await call(sdk.disconnectRepo({ path: { workspace: ws } }));
      toast.success("Disconnected. The workspace's files stay.");
      setPulled(null);
      await qc.invalidateQueries({ queryKey: ["ws", ws, "repo"] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Panel title="GitHub repository">
      <p className="text-muted-foreground mb-3 text-[13px]">
        Keep this workspace's sopc project in a repository: pull it into drafts here, and apply suggestions as pull requests instead of drafts.
      </p>
      {res.isPending ? (
        <Note>Loading…</Note>
      ) : res.isError ? (
        <RepoError error={res.error} />
      ) : !r || editing ? (
        <ConnectForm current={r ?? null} onDone={() => setEditing(false)} onCancel={() => setEditing(false)} />
      ) : (
        <div className="grid gap-3">
          <RepoLine r={r} />
          <Note>{r.pulled_sha ? `Last pulled ${r.pulled_sha.slice(0, 7)}, ${when(r.pulled_at)}.` : "Not pulled yet."}</Note>
          {error != null && <RepoError error={error} />}
          {pulled && (
            <Banner tone="ok">
              {pulled.unchanged ? (
                <>
                  Already up to date with <code>{pulled.sha.slice(0, 7)}</code>.
                </>
              ) : (
                <>
                  Pulled <code>{pulled.sha.slice(0, 7)}</code>: {pulled.changed.length} file{pulled.changed.length === 1 ? "" : "s"} changed, as drafts.{" "}
                  <HrefLink href={wsHref(ws, "changes")}>Review them in Unpublished changes</HrefLink>.
                </>
              )}
            </Banner>
          )}
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => void pull()} disabled={!!busy} title={`Make the drafts match ${r.base_branch}'s head`}>
              {busy === "pull" && <Loader2 className="animate-spin" />} Pull from repo
            </Button>
            <Button variant="outline" onClick={() => setEditing(true)} disabled={!!busy}>
              Edit connection
            </Button>
            <ConfirmButton variant="ghost" className="text-err" confirm="Really disconnect?" disabled={!!busy} onConfirm={() => void disconnect()}>
              Disconnect
            </ConfirmButton>
          </div>
          <Note className="text-xs">Pulling makes the drafts match the branch: files not in the repository are deleted from the drafts. Nothing is live until you publish.</Note>
        </div>
      )}
    </Panel>
  );
}
