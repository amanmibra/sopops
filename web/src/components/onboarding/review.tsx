// Import agents, step 2: progress (and the kickoff for Claude Code or Codex), then review the proposal in steps and
// apply it as drafts. Once the proposal is ready, progress and the kickoff fold into one line.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, ExternalLink } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import type { EventOut, ImportOut, Kickoff } from "@/client/types.gen";
import { call, sdk } from "@/lib/api";
import { platformName, plural, when } from "@/lib/kinds";
import { listNames, usePlatforms } from "@/lib/platforms";
import { useRepoStream } from "@/lib/live";
import { TERMINAL, iq, kickoffFor } from "@/lib/onboarding";
import { cn } from "@/lib/utils";
import { settingsHref, useWs, wsHref } from "@/lib/workspace";
import { Banner, ConfirmButton, ErrorBanner, Note, PageTitle, Panel, Segmented, Sub, Tag } from "../bits";
import { GitHubIcon } from "../brand-icon";
import { ConnectGuide } from "../connect-guide";
import { type ReviewPlace, Review, capital, madeBy } from "./proposal";
import { SopcIntro, SopcReview } from "./sopc";
import { HrefLink } from "../href-link";
import { Page } from "../item-shell";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";

const STATUS: Record<ImportOut["status"], string> = {
  waiting: "waiting for your agent",
  running: "working",
  ready: "ready to review",
  failed: "failed",
  applied: "applied as drafts",
  discarded: "discarded",
};

export function ImportView({ id }: { id: string }) {
  const { ws } = useWs();
  const imp = useQuery(iq.one(ws, id));
  const [place, setPlace] = useState<ReviewPlace>({ step: "found", reached: "found", focus: false });
  if (imp.isPending) return <Page>Loading…</Page>;
  if (imp.isError)
    return (
      <Page>
        <ErrorBanner error={imp.error} />
      </Page>
    );
  const d = imp.data;
  const live = !TERMINAL.includes(d.status) && d.status !== "failed" && (d.status !== "ready" || !!d.token?.active);
  const kickoff = kickoffFor(id);
  return (
    <Page wide>
      <PageTitle aside={<Tag tone={d.status === "failed" ? "removed" : d.status === "ready" ? "live" : undefined}>{STATUS[d.status]}</Tag>}>Import agents</PageTitle>
      <Sub>
        {d.source === "sopc" ? <SopcIntro imp={d} /> : d.source === "agent" ? "Claude Code or Codex extracts the instructions in your repo and sends them here." : "sopops is extracting the instructions with its model."}{" "}
        {d.agents.length ? `${plural(d.agents.length, "agent", "agents")}: ${d.agents.length > 8 ? `${d.agents.slice(0, 8).join(", ")} and ${d.agents.length - 8} more` : d.agents.join(", ")}.` : ""}
        {d.mode === "repo" && d.repo ? ` Repo mode: ${d.repo}${d.repo_branch ? ` on ${d.repo_branch}` : ""}.` : ""}
      </Sub>
      <OnboardingPullRequest imp={d} />
      <Folded imp={d} live={live} folded={!!d.proposal && d.status === "ready"}>
        {d.source === "agent" && !TERMINAL.includes(d.status) && <TokenPanel imp={d} kickoff={kickoff?.kickoff} token={kickoff?.token} />}
        <ProgressLog id={id} live={live} />
      </Folded>
      {d.status === "failed" && (
        <Banner tone="err">
          {d.error || "The import failed."} <HrefLink href={wsHref(ws, "import")}>Start again</HrefLink>
        </Banner>
      )}
      {d.status === "applied" && d.pr_state !== "merged" && (
        <Banner tone="ok">
          Applied as drafts. <HrefLink href={wsHref(ws, "changes")}>Review the unpublished changes</HrefLink>, then publish.
        </Banner>
      )}
      {(d.status === "applied" || d.pr_state === "merged") && (
        <Panel title="Connect your agents">
          <p className="text-muted-foreground mb-3 max-w-prose text-[13px] leading-relaxed">
            After you publish, each agent needs two things: its instructions from sopops, and a way to send its sessions back for checks. Here's what each
            platform needs.
          </p>
          <ConnectGuide />
        </Panel>
      )}
      {d.status === "discarded" && <Banner>This import was discarded. Nothing changed.</Banner>}
      {d.proposal && d.status === "ready" && (d.source === "sopc" ? <SopcReview imp={d} proposal={d.proposal} /> : <Review key={`${d.pushes}:${d.updated_at}`} imp={d} proposal={d.proposal} place={place} onPlace={setPlace} />)}
    </Page>
  );
}

// --- the onboarding pull request (repo mode) ------------------------------------------------------

const PR_STATE: Record<NonNullable<ImportOut["pr_state"]>, string> = { open: "waiting for your review", merged: "merged", closed: "closed" };

function OnboardingPullRequest({ imp }: { imp: ImportOut }) {
  const { ws } = useWs();
  useRepoStream(ws); // the pull request merging arrives on the workspace's stream
  if (imp.mode !== "repo" || imp.source !== "agent") return null;
  const folder = imp.repo_path ? `${imp.repo_path}/` : "the repository root";
  if (!imp.pr_url) {
    if (imp.repo_project === "valid" || TERMINAL.includes(imp.status)) return null;
    return (
      <Banner>
        Your agent will propose a pull request {imp.repo_project === "invalid" ? "replacing" : "adding"} <code>{folder}</code> in {imp.repo}
        {imp.repo_branch ? (
          <>
            {" "}
            on <code>{imp.repo_branch}</code>
          </>
        ) : null}
        . Nothing merges without you. It shows here once your agent opens it.
      </Banner>
    );
  }
  return (
    <>
      {imp.pr_state === "merged" &&
        (imp.error ? (
          <Banner tone="err">
            {imp.error} <HrefLink href={settingsHref(ws, "github")}>Pull it from Settings</HrefLink>.
          </Banner>
        ) : (
          <Banner tone="ok">
            <b>Your onboarding PR merged.</b> Its <code>{folder}</code> is now your drafts. <HrefLink href={wsHref(ws, "changes")}>Publish your first release</HrefLink>.
          </Banner>
        ))}
      {imp.pr_state === "closed" && <Banner tone="warn">The onboarding pull request was closed without merging. Nothing was pulled.</Banner>}
      <Panel title="Onboarding pull request">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <a href={imp.pr_url} target="_blank" rel="noreferrer" className="text-primary inline-flex items-center gap-1 font-semibold break-all hover:underline">
            <GitHubIcon size={14} /> {imp.pr_url.replace(/^https?:\/\/[^/]+\//, "")} <ExternalLink className="size-3.5 shrink-0" />
          </a>
          {imp.pr_state && <Tag tone={imp.pr_state === "merged" ? "live" : imp.pr_state === "closed" ? "removed" : undefined}>{PR_STATE[imp.pr_state]}</Tag>}
        </div>
        {imp.pr_state === "open" && (
          <Note className="mt-1.5">
            It adds <code>{folder}</code> to {imp.repo}. Nothing merges without you: review and merge it on GitHub. When it merges, sopops pulls <code>{folder}</code>{" "}
            into drafts, right away when GitHub tells it, or within a minute.
          </Note>
        )}
      </Panel>
    </>
  );
}

// --- progress and kickoff ----------------------------------------------------------------------

/** Once the proposal is ready: "Claude Code finished at 3:42 PM · Show progress", opening the progress and kickoff. */
function Folded({ imp, live, folded, children }: { imp: ImportOut; live: boolean; folded: boolean; children: ReactNode }) {
  const { ws } = useWs();
  const events = useQuery({ ...iq.events(ws, imp.id, live), enabled: folded });
  const [open, setOpen] = useState(false);
  if (!folded) return <>{children}</>;
  const last = events.data?.at(-1)?.at ?? imp.updated_at;
  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
        <span className="text-muted-foreground">
          {capital(madeBy(imp))} finished at {new Date(last).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
        </span>
        <span className="text-muted-foreground" aria-hidden>
          ·
        </span>
        <button type="button" aria-expanded={open} className="text-primary cursor-pointer font-semibold hover:underline" onClick={() => setOpen(!open)}>
          {open ? "Hide progress" : "Show progress"}
        </button>
      </div>
      {open && children}
    </>
  );
}

function ProgressLog({ id, live }: { id: string; live: boolean }) {
  const { ws } = useWs();
  const events = useQuery(iq.events(ws, id, live));
  const rows: EventOut[] = events.data ?? [];
  return (
    <Panel title="Progress">
      {rows.length ? (
        <ol className="grid max-h-64 gap-1 overflow-auto text-[13px]" aria-live="polite">
          {rows.map((e) => (
            <li key={e.id} className="grid grid-cols-[72px_96px_1fr] gap-2">
              <span className="text-muted-foreground tabular-nums">{new Date(e.at).toLocaleTimeString()}</span>
              <span className={cn("font-semibold", e.by === "agent" ? "text-k-procedure" : "text-muted-foreground")}>
                {e.by === "agent" ? (e.client ?? "your agent") : "sopops"}
              </span>
              <span>{e.message}</span>
            </li>
          ))}
        </ol>
      ) : (
        <Note>Nothing yet.</Note>
      )}
    </Panel>
  );
}

function CopyBlock({ label, text, rows }: { label: string; text: string; rows?: number }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setDone(true);
    setTimeout(() => setDone(false), 1500);
  };
  return (
    <div className="mb-3">
      <div className="mb-1 flex items-center gap-2">
        <span className="font-semibold">{label}</span>
        <Button size="xs" variant="outline" onClick={copy} aria-label={`Copy ${label}`}>
          {done ? <Check /> : <Copy />} {done ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre className="bg-muted overflow-x-auto rounded-md px-3 py-2 font-mono text-[12.5px] whitespace-pre-wrap break-all" style={rows ? { maxHeight: `${rows * 1.5}em` } : undefined}>
        {text}
      </pre>
    </div>
  );
}

function EditableCopy({ label, initial, help }: { label: string; initial: string; help?: string }) {
  const [text, setText] = useState(initial);
  const [done, setDone] = useState(false);
  return (
    <div className="mb-3">
      <div className="mb-1 flex items-center gap-2">
        <span className="font-semibold">{label}</span>
        <Button
          size="xs"
          variant="outline"
          onClick={async () => {
            await navigator.clipboard.writeText(text);
            setDone(true);
            setTimeout(() => setDone(false), 1500);
          }}
        >
          {done ? <Check /> : <Copy />} {done ? "Copied" : "Copy"}
        </Button>
      </div>
      {help && <p className="text-muted-foreground mb-1.5 text-[13px]">{help}</p>}
      <Textarea aria-label={label} className="font-mono text-[12.5px]" value={text} onChange={(e) => setText(e.target.value)} />
    </div>
  );
}

function TokenPanel({ imp, kickoff, token }: { imp: ImportOut; kickoff?: Kickoff; token?: string }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [tool, setTool] = useState<"claude" | "codex">("claude");
  const t = imp.token;
  const finish = async () => {
    await call(sdk.revokeImportToken({ path: { workspace: ws, id: imp.id } }));
    await qc.invalidateQueries({ queryKey: ["ws", ws, "import", imp.id] });
    toast.success("The onboarding token no longer works.");
  };
  if (!t) return null;
  if (!t.active)
    return (
      <Panel title="Onboarding token">
        <Note>
          The token {t.revoked_at ? "was ended" : "expired"}. Your agent can't send more. {imp.proposal ? "Review what it sent below." : ""}{" "}
          <HrefLink href={wsHref(ws, "import")}>Start a new one</HrefLink>
        </Note>
      </Panel>
    );
  return (
    <Panel
      title={
        <span className="flex flex-wrap items-center gap-2">
          Start your coding agent
          <Note>
            token {t.prefix}…, works until {when(t.expires_at)}
          </Note>
          <span className="flex-1" />
          <ConfirmButton size="sm" variant="outline" confirm="End the token?" onConfirm={finish}>
            Finish
          </ConfirmButton>
        </span>
      }
    >
      {kickoff && token ? (
        <details open={!imp.proposal}>
          <summary className="text-muted-foreground mb-2 cursor-pointer text-[13px]">{imp.proposal ? "Commands and instructions" : "How to start"}</summary>
          <p className="mb-3 max-w-[80ch] text-[13px]">
            In a terminal at the root of your agents' repo, run this. It adds the sopops tools to your coding agent and starts it on the onboarding instructions. The token in it is shown only now.
          </p>
          <div className="mb-3">
            <Segmented
              label="Coding agent"
              value={tool}
              onChange={setTool}
              options={[
                ["claude", "Claude Code"],
                ["codex", "Codex"],
              ]}
            />
          </div>
          <CopyBlock label="Run in your agents' repo" text={tool === "claude" ? kickoff.claude_code : kickoff.codex} />
          {tool === "codex" && (
            <details className="mb-3">
              <summary className="text-muted-foreground cursor-pointer text-[13px]">Or add the server to ~/.codex/config.toml</summary>
              <CopyBlock label="config.toml" text={kickoff.codex_toml} />
            </details>
          )}
          <EditableCopy
            key={tool}
            label="Recommended settings"
            initial={tool === "claude" ? kickoff.settings_claude_code : kickoff.settings_codex}
            help="Suggestions; edit them for your setup."
          />
        </details>
      ) : (
        <Note>
          The commands and token were shown when the token was created. Lost them? <HrefLink href={wsHref(ws, "import")}>Create a new token</HrefLink> (end this one with Finish).
        </Note>
      )}
    </Panel>
  );
}

export function ImportCard({ children }: { children?: ReactNode }) {
  const { ws } = useWs();
  const plats = usePlatforms();
  const from = ["Claude Code or Codex in your repo", "pasted or uploaded files", ...plats.shown.filter((p) => p !== "livekit").map(platformName)];
  return (
    <Panel title="Import agents">
      <p className="mb-3 text-[13px]">
        Bring in the instructions your agents run today: from {listNames(from)}. You review the result before it's saved as drafts.
      </p>
      {children}
      <HrefLink href={wsHref(ws, "import")}>Import agents</HrefLink>
    </Panel>
  );
}
