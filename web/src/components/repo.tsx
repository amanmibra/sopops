// Settings: the workspace's GitHub repository. Connect it through the GitHub App (install it, pick the repository) or
// with an access token (write-only), keep drafts and the folder in sync both ways, settle sync conflicts, disconnect.
// Suggestions open pull requests on it. Onboarding reuses the connect form and the connection line.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2 } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import {
  ApiError, type ConflictOut, type GitHubAppOut, type RepoOut, type RepoProjectOut, type SendOut, type SyncOut, call, refresh, sdk,
} from "@/lib/api";
import { when } from "@/lib/kinds";
import { useRepoStream } from "@/lib/live";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ConfirmButton, ErrorBanner, Note, Panel } from "./bits";
import { GitHubIcon } from "./brand-icon";
import { author } from "./form";
import { HrefLink } from "./href-link";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Textarea } from "./ui/textarea";

/** The workspace's repository, or null when none is connected. */
export const repoQuery = (ws: string) => ({
  queryKey: ["ws", ws, "repo"],
  queryFn: async (): Promise<RepoOut | null> => (await call(sdk.getRepo({ path: { workspace: ws } }))) ?? null,
});

/** Whether a GitHub App is set up, and whether one can be created from here. */
export const githubAppQuery = () => ({
  queryKey: ["github", "app"],
  queryFn: (): Promise<GitHubAppOut> => call(sdk.getGithubApp()),
  staleTime: 60_000,
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
      {status === 503 && " Set SOPOPS_SECRET_KEY where sopops runs, so credentials can be stored encrypted."}
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


type FormProps = { current: RepoOut | null; onDone: (r: RepoOut) => void; onCancel?: () => void; requireProject?: boolean };

/** What GitHub sent back in the address after installing the App or creating one (`?github_installation=`,
 * `?github_app=created`, `?github_error=`), read once and then taken out of the address. */
function useGitHubReturn() {
  const [ret] = useState(() => {
    const url = new URL(window.location.href);
    const out = { installation: url.searchParams.get("github_installation"), app: url.searchParams.get("github_app"), error: url.searchParams.get("github_error") };
    if (out.installation || out.app || out.error) {
      for (const k of ["github_installation", "github_app", "github_error", "setup_action"]) url.searchParams.delete(k);
      window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    }
    return out;
  });
  return ret;
}

/** Connect the repository, or change the connection: through the GitHub App when one is set up, else with an access
 * token (and, when sopops can, an offer to create the App). `requireProject: false` (onboarding) connects before the
 * folder holds a sopc project. */
export function ConnectForm(props: FormProps) {
  const app = useQuery(githubAppQuery());
  const ret = useGitHubReturn();
  const [useToken, setUseToken] = useState(props.current?.auth === "token");
  if (app.isPending) return <Note>Loading…</Note>;
  const configured = !!app.data?.configured;
  return (
    <div className="grid gap-3">
      {ret.error && (
        <Banner tone="err">
          <b>GitHub sent you back with a problem:</b> {ret.error}
        </Banner>
      )}
      {ret.app === "created" && configured && <Banner tone="ok">The GitHub App is created. Install it on the account that owns your repository.</Banner>}
      {configured && !useToken ? (
        <AppConnect {...props} app={app.data!} installation={ret.installation ? Number(ret.installation) : null} />
      ) : (
        <TokenForm {...props} />
      )}
      {configured ? (
        <div>
          <Button variant="link" size="xs" className="h-auto px-0" onClick={() => setUseToken(!useToken)}>
            {useToken ? "Use the GitHub App instead" : "Use an access token instead"}
          </Button>
        </div>
      ) : (
        app.data?.can_create && <CreateApp />
      )}
    </div>
  );
}

/** Install the App (GitHub comes back with the installation), then pick the repository it can reach, and the folder. */
function AppConnect({ current, onDone, onCancel, requireProject = true, app, installation }: FormProps & { app: GitHubAppOut; installation: number | null }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const installs = useQuery({
    queryKey: ["ws", ws, "github", "installations"],
    queryFn: () => call(sdk.listGithubInstallations({ path: { workspace: ws } })),
  });
  const [picked, setPicked] = useState<number | null>(installation ?? current?.installation_id ?? null);
  const inst = picked ?? installs.data?.[0]?.installation_id ?? null;
  const repos = useQuery({
    queryKey: ["ws", ws, "github", "installations", inst, "repos"],
    queryFn: () => call(sdk.listInstallationRepositories({ path: { workspace: ws, installation_id: inst! } })),
    enabled: inst != null,
  });
  const [full, setFull] = useState(current ? `${current.owner}/${current.repo}` : "");
  const [branch, setBranch] = useState(current?.base_branch ?? "");
  const [path, setPath] = useState(current?.path ?? "sops");
  const [busy, setBusy] = useState<null | "install" | "connect">(null);
  const [error, setError] = useState<unknown>(null);
  const chosen = repos.data?.find((r) => r.full_name === full) ?? null;

  const install = async () => {
    setBusy("install");
    setError(null);
    try {
      const { url } = await call(sdk.githubInstallUrl({ path: { workspace: ws }, query: { return_to: window.location.pathname } }));
      window.location.assign(url);
    } catch (e) {
      setError(e);
      setBusy(null);
    }
  };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!chosen || inst == null) return;
    setBusy("connect");
    setError(null);
    try {
      const r = await call(
        sdk.connectRepo({
          path: { workspace: ws },
          body: {
            owner: chosen.owner, repo: chosen.repo, installation_id: inst, token: null, base_branch: branch.trim() || chosen.default_branch,
            path: path.trim(), require_project: requireProject,
          },
        }),
      );
      toast.success(`Connected ${r.owner}/${r.repo} through the GitHub App.`);
      await qc.invalidateQueries({ queryKey: ["ws", ws, "repo"] });
      onDone(r);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };
  const installButton = (label: string, variant?: "outline" | "ghost") => (
    <Button type="button" variant={variant} disabled={!!busy} onClick={() => void install()}>
      {busy === "install" ? <Loader2 className="animate-spin" /> : <GitHubIcon size={15} />} {label}
    </Button>
  );

  if (installs.isPending) return <Note>Loading…</Note>;
  if (!installs.data?.length)
    return (
      <div className="grid gap-3">
        {error != null && <RepoError error={error} />}
        <p className="text-[13px]">
          Install {app.name ? <b>{app.name}</b> : "the sopops GitHub App"} on the account that owns the repository and give it that repository. GitHub brings you back
          here to pick it.
        </p>
        <div className="flex flex-wrap gap-2">
          {installButton("Install on GitHub")}
          {current && onCancel && (
            <Button type="button" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          )}
        </div>
      </div>
    );
  return (
    <form onSubmit={(e) => void submit(e)} className="grid gap-3">
      {error != null && <RepoError error={error} />}
      {repos.isError && <RepoError error={repos.error} />}
      <div className="grid gap-3 sm:grid-cols-2">
        {installs.data.length > 1 && (
          <Field id="repo-installation" label="Account">
            <Select value={String(inst)} onValueChange={(v) => (setPicked(Number(v)), setFull(""))}>
              <SelectTrigger id="repo-installation" className="h-9 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {installs.data.map((i) => (
                  <SelectItem key={i.installation_id} value={String(i.installation_id)}>
                    {i.account ?? `Installation ${i.installation_id}`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        )}
        <Field id="repo-full" label="Repository" hint={repos.data && !repos.data.length ? "The App can't reach any repository yet. Add one on GitHub." : undefined}>
          <Select value={full} onValueChange={(v) => (setFull(v), setBranch(""))} disabled={!repos.data?.length}>
            <SelectTrigger id="repo-full" className="h-9 w-full">
              <SelectValue placeholder={repos.isPending ? "Loading…" : "Pick a repository"} />
            </SelectTrigger>
            <SelectContent>
              {repos.data?.map((r) => (
                <SelectItem key={r.full_name} value={r.full_name}>
                  {r.full_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field id="repo-app-branch" label="Base branch" hint="Synced from, and pull requests target it">
          <Input id="repo-app-branch" className="h-9" value={branch} placeholder={chosen?.default_branch ?? "main"} onChange={(e) => setBranch(e.target.value)} />
        </Field>
        <Field
          id="repo-app-path"
          label="Folder"
          hint={requireProject ? "The sopc project (with sopc.yaml); empty for the root" : "Where the sopc project is, or will go; empty for the root"}
        >
          <Input id="repo-app-path" className="h-9" value={path} onChange={(e) => setPath(e.target.value)} />
        </Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={!!busy || !chosen}>
          {busy === "connect" && <Loader2 className="animate-spin" />} {current ? "Save" : "Connect"}
        </Button>
        {installButton("Add a repository or account", "ghost")}
        {current && onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}

/** The access token form: the fallback when there's no GitHub App, or by choice. */
function TokenForm({ current, onDone, onCancel, requireProject = true }: FormProps) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const asToken = current?.auth === "token" ? current : null;
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
        <Field id="repo-base_branch" label="Base branch" hint="Synced from, and pull requests target it" error={fields.base_branch}>
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
        hint={asToken?.token_hint ? `Stored, ending in ${asToken.token_hint}. Leave empty to keep it.` : "Stored encrypted and never shown again."}
      >
        {input("token", {
          type: "password",
          autoComplete: "new-password",
          placeholder: asToken?.token_hint ? `••••${asToken.token_hint.replace(/^…/, "")}` : "github_pat_…",
          required: !asToken,
        })}
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

/** Self-hosted, with no App yet: create one with GitHub's App manifest flow. GitHub asks to confirm, then comes back
 * here and sopops stores the App's key and webhook secret encrypted. */
function CreateApp() {
  const [open, setOpen] = useState(false);
  const [org, setOrg] = useState("");
  const [hook, setHook] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const got = await call(
        sdk.githubAppManifest({
          body: { public_url: window.location.origin, return_to: window.location.pathname, organization: org.trim() || null, webhook_url: hook.trim() || null },
        }),
      );
      const form = document.createElement("form");
      form.method = "post";
      form.action = got.action;
      const field = document.createElement("input");
      field.type = "hidden";
      field.name = "manifest";
      field.value = got.manifest;
      form.appendChild(field);
      document.body.appendChild(form);
      form.submit();
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };
  if (!open)
    return (
      <div className="text-muted-foreground text-[13px]">
        Or skip the token: <Button variant="link" size="xs" className="h-auto px-0" onClick={() => setOpen(true)}>create a GitHub App for this server</Button>. It
        signs in without a personal token, and GitHub tells sopops about changes as they happen.
      </div>
    );
  return (
    <div className="grid gap-3 rounded-md border p-3">
      <div className="text-[13px]">
        <b>Create a GitHub App.</b> GitHub shows you what it asks for (Contents and Pull requests: read and write, Metadata: read) and creates it. sopops then stores
        its key encrypted. Install it on your repository next.
      </div>
      {error != null && <RepoError error={error} />}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field id="app-org" label="Organization" hint="Leave empty to create it in your own account">
          <Input id="app-org" className="h-9" value={org} placeholder="acme" onChange={(e) => setOrg(e.target.value)} />
        </Field>
        <Field id="app-hook" label="Webhook address" hint="GitHub can't reach 127.0.0.1. Leave empty, or use a smee.io channel to get changes as they happen.">
          <Input id="app-hook" className="h-9" value={hook} placeholder={`${window.location.origin}/v1/github/webhook`} onChange={(e) => setHook(e.target.value)} />
        </Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button disabled={busy} onClick={() => void create()}>
          {busy ? <Loader2 className="animate-spin" /> : <GitHubIcon size={15} />} Create on GitHub
        </Button>
        <Button variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** The connection on one line: the folder on GitHub, the branch, how sopops signs in, and an optional action. */
export function RepoLine({ r, children }: { r: RepoOut; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <GitHubIcon className="text-muted-foreground" />
      <a href={r.url} target="_blank" rel="noreferrer" className="text-primary inline-flex items-center gap-1 font-semibold break-all hover:underline">
        {r.owner}/{r.repo}
        {r.path ? `/${r.path}` : ""} <ExternalLink className="size-3.5 shrink-0" />
      </a>
      <span className="text-muted-foreground text-[13px]">
        on <code className="text-[12.5px]">{r.base_branch}</code> · {r.auth === "app" ? "through the GitHub App" : `token ••••${(r.token_hint ?? "").replace(/^…/, "")}`}
      </span>
      {children}
    </div>
  );
}

const PR_NUMBER = (url: string) => url.match(/\/pull\/(\d+)/)?.[1];

export function RepoCard() {
  const { ws } = useWs();
  const qc = useQueryClient();
  useRepoStream(ws);
  // While GitHub isn't sending webhooks (a server on 127.0.0.1), reading the connection is what checks for changes.
  const res = useQuery({ ...repoQuery(ws), refetchInterval: (q) => (q.state.data && !q.state.data.webhook_at ? 60_000 : false) });
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<null | "sync" | "send" | "disconnect">(null);
  const [error, setError] = useState<unknown>(null);
  const [synced, setSynced] = useState<SyncOut | null>(null);
  const [sent, setSent] = useState<SendOut | null>(null);
  const r = res.data;

  const run = async (what: "sync" | "send" | "disconnect", fn: () => Promise<void>) => {
    setBusy(what);
    setError(null);
    setSynced(null);
    setSent(null);
    try {
      await fn();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  const sync = () =>
    run("sync", async () => {
      setSynced(await call(sdk.syncRepo({ path: { workspace: ws }, query: { author: author() } })));
      await refresh(qc, ws);
    });
  const send = () =>
    run("send", async () => {
      setSent(await call(sdk.sendRepoDrafts({ path: { workspace: ws } })));
      await refresh(qc, ws);
    });
  const disconnect = () =>
    run("disconnect", async () => {
      await call(sdk.disconnectRepo({ path: { workspace: ws } }));
      toast.success("Disconnected. The workspace's files stay.");
      await qc.invalidateQueries({ queryKey: ["ws", ws, "repo"] });
    });

  return (
    <Panel title="GitHub repository">
      <p className="text-muted-foreground mb-3 text-[13px]">
        Keep this workspace's sopc project in a repository: changes on GitHub come into drafts here, drafts go back as a pull request, and suggestions are applied as pull
        requests instead of drafts.
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
          {r.broken && (
            <Banner tone="err">
              <b>The connection stopped working.</b> {r.broken}
            </Banner>
          )}
          <Note>
            {r.pulled_sha ? (
              <>
                Last synced <code>{r.pulled_sha.slice(0, 7)}</code>, {when(r.pulled_at)}.
                {r.webhook_at ? " GitHub sends changes here as they happen." : " Checked for changes every minute while this page is open."}
              </>
            ) : (
              "Not synced yet. Sync now brings the folder into drafts; after that, changes on GitHub come in by themselves."
            )}
          </Note>
          {r.sync_error && (
            <Banner tone="warn">
              <b>The last sync didn't go through.</b> {r.sync_error}
            </Banner>
          )}
          {r.conflicts.length > 0 && <Conflicts r={r} />}
          {r.sync_pr && <SyncPr url={r.sync_pr.url} state={r.sync_pr.state} />}
          {error != null && (error instanceof ApiError && error.status === 422 ? <ErrorBanner error={error} lead="GitHub's files don't build with the drafts:" /> : <RepoError error={error} />)}
          {synced && !synced.error && (
            <Banner tone="ok">
              {synced.unchanged ? (
                <>
                  Already up to date with <code>{synced.sha.slice(0, 7)}</code>.
                </>
              ) : (
                <>
                  Synced <code>{synced.sha.slice(0, 7)}</code>: {synced.changed.length} file{synced.changed.length === 1 ? "" : "s"} changed in drafts
                  {synced.merged.length ? `, ${synced.merged.length} merged with edits made here` : ""}.{" "}
                  <HrefLink href={wsHref(ws, "changes")}>Review them in Unpublished changes</HrefLink>.
                </>
              )}
            </Banner>
          )}
          {sent && (
            <Banner tone="ok">
              {sent.updated ? "Updated" : "Opened"}{" "}
              <a href={sent.url} target="_blank" rel="noreferrer" className="font-semibold underline">
                pull request #{PR_NUMBER(sent.url)}
              </a>{" "}
              with {sent.files.length} file{sent.files.length === 1 ? "" : "s"}. Merge it on GitHub.
            </Banner>
          )}
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => void sync()} disabled={!!busy || !!r.broken} title={`Bring changes on ${r.base_branch} into drafts`}>
              {busy === "sync" && <Loader2 className="animate-spin" />} Sync now
            </Button>
            <Button variant="outline" onClick={() => void send()} disabled={!!busy || !!r.broken || !r.pulled_sha} title="Open one pull request with the drafts">
              {busy === "send" ? <Loader2 className="animate-spin" /> : <GitHubIcon size={15} />} Send drafts to GitHub
            </Button>
            <Button variant="outline" onClick={() => setEditing(true)} disabled={!!busy}>
              Edit connection
            </Button>
            <ConfirmButton variant="ghost" className="text-err" confirm="Really disconnect?" disabled={!!busy} onConfirm={() => void disconnect()}>
              Disconnect
            </ConfirmButton>
          </div>
          <Note className="text-xs">
            Sync now brings changes on {r.base_branch} into drafts and merges them with edits made here. Send drafts to GitHub opens one pull request with what differs.
            Nothing is live until you publish.
          </Note>
        </div>
      )}
    </Panel>
  );
}

function SyncPr({ url, state }: { url: string; state: string | null }) {
  const link = (
    <a href={url} target="_blank" rel="noreferrer" className="text-primary inline-flex items-center gap-1 font-semibold hover:underline">
      <GitHubIcon size={14} /> pull request #{PR_NUMBER(url)}
    </a>
  );
  if (state === "open") return <Note>Drafts sent to GitHub: {link} is waiting for review. Sending again updates it.</Note>;
  if (state === "merged") return <Note>Your sync {link} merged.</Note>;
  return <Note>Your sync {link} was closed without merging.</Note>;
}

/** Files changed both on GitHub and here that don't merge: keep yours, take GitHub's, or edit. */
function Conflicts({ r }: { r: RepoOut }) {
  return (
    <Banner tone="warn">
      <div className="grid gap-2">
        <b>
          {r.conflicts.length === 1 ? "A file" : `${r.conflicts.length} files`} changed on GitHub and here in different ways. The drafts are left as they are until you
          choose.
        </b>
        {r.conflicts.map((c) => (
          <ConflictRow key={c.path} folder={r.path} path={c.path} deleted={c.deleted_on_github} />
        ))}
      </div>
    </Banner>
  );
}

function ConflictRow({ folder, path, deleted }: { folder: string; path: string; deleted: boolean }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [editing, setEditing] = useState<ConflictOut | null>(null);
  const [text, setText] = useState("");
  const shown = folder ? `${folder}/${path}` : path;
  const resolve = async (take: "mine" | "github" | "edited") => {
    setBusy(true);
    setError(null);
    try {
      await call(sdk.resolveRepoConflict({ path: { workspace: ws }, body: { path, take, content: take === "edited" ? text : null, author: author() } }));
      await refresh(qc, ws);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const edit = async () => {
    setBusy(true);
    setError(null);
    try {
      const got = await call(sdk.getRepoConflict({ path: { workspace: ws }, query: { path } }));
      setText(got.ours ?? got.theirs ?? "");
      setEditing(got);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid gap-1.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span>
          <code>{shown}</code> {deleted ? "was deleted on GitHub and changed here." : "changed on GitHub and here."}
        </span>
        <Button size="xs" variant="outline" disabled={busy} onClick={() => void resolve("mine")}>
          Keep yours
        </Button>
        <Button size="xs" variant="outline" disabled={busy} onClick={() => void resolve("github")}>
          {deleted ? "Delete it" : "Take GitHub's"}
        </Button>
        {!editing && (
          <Button size="xs" variant="ghost" disabled={busy} onClick={() => void edit()}>
            Edit
          </Button>
        )}
      </div>
      {error != null && (error instanceof ApiError && error.status === 422 ? <ErrorBanner error={error} lead="This doesn't build yet:" /> : <RepoError error={error} />)}
      {editing && (
        <div className="grid gap-2">
          <Textarea className="font-mono text-[12.5px]" rows={10} value={text} onChange={(e) => setText(e.target.value)} aria-label={`${shown}, as you want it`} />
          {editing.theirs != null && (
            <details className="text-[13px]">
              <summary className="cursor-pointer">GitHub's version</summary>
              <pre className="bg-muted mt-1 max-h-64 overflow-auto rounded-md p-2 text-[12px] whitespace-pre-wrap">{editing.theirs}</pre>
            </details>
          )}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy} onClick={() => void resolve("edited")}>
              {busy && <Loader2 className="animate-spin" />} Save this version
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
