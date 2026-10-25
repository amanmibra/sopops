// Import agents, one question at a time: where the instructions should live, where they are now, then the one action
// for that route. Each answer narrows what comes next, so a route the earlier answers rule out is never offered.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload, X } from "lucide-react";
import { type ReactNode, useRef, useState } from "react";
import { call, sdk } from "@/lib/api";
import { PLATFORMS, plural, when } from "@/lib/kinds";
import { iq, readPromptFiles, rememberKickoff } from "@/lib/onboarding";
import { type Platforms, usePlatforms } from "@/lib/platforms";
import { cn } from "@/lib/utils";
import { settingsHref, useWs, wsHref } from "@/lib/workspace";
import { Banner, ErrorBanner, Note, PageTitle, Panel, Sub, Tag } from "../bits";
import { GitHubIcon, PlatformIcon, PlatformLabel } from "../brand-icon";
import { author } from "../form";
import { HrefLink, useGo } from "../href-link";
import { Page } from "../item-shell";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Input } from "../ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";
import { Textarea } from "../ui/textarea";
import { repoProjectQuery, repoQuery } from "../repo";
import { ProjectFinding, RepoConnect } from "./github";
import { SopcSource } from "./sopc";
import { apiOrigin } from "@/lib/paths";

type Where = "sopops" | "repo";
type Source = "agent" | "paste" | "elevenlabs" | "vapi" | "sopc";
type Platform = "livekit" | "elevenlabs" | "vapi";
type Row = { name: string; platform: Platform; platform_id: string; tools: string; prompt: string };
type Choice<T extends string> = { value: T; title: string; text: string; note?: string; icon?: ReactNode; disabled?: boolean };

const IMPORT_PLATFORMS = PLATFORMS.filter((p) => p.value !== "retell");
const blank = (platform: Platform = "livekit"): Row => ({ name: "", platform, platform_id: "", tools: "", prompt: "" });

const WHERE: Choice<Where>[] = [
  { value: "sopops", title: "In sopops", text: "You edit and publish them here, and your repository doesn't change." },
  {
    value: "repo",
    title: "In sopops and your repository — Coming soon",
    text: "They live as a sopc project in your GitHub repository, and fixes arrive as pull requests.",
    icon: <GitHubIcon size={14} />,
    disabled: true,
  },
];
/** The routes left once a team says where the instructions should live: a sopc project only for a repository, and a
 * platform only for a team that runs it. */
function sources(plats: Platforms, where: Where): Choice<Source>[] {
  return [
    { value: "agent", title: "Claude Code or Codex", text: "Recommended. Your coding agent finds them wherever they live in your code and sends them here." },
    {
      value: "paste",
      title: "Paste or upload",
      text: plats.has("livekit")
        ? "LiveKit instructions from your code, or any instructions as text. One agent per text or file."
        : "Any instructions as text. One agent per text or file.",
    },
    ...(where === "repo" ? [{ value: "sopc" as const, title: "A sopc project you keep already", text: "Bring it in as it is, from your repository or a folder." }] : []),
    ...(plats.has("elevenlabs") ? [{ value: "elevenlabs" as const, title: "ElevenLabs", text: "Read your agents' instructions and tools with an API key." }] : []),
    ...(plats.has("vapi") ? [{ value: "vapi" as const, title: "Vapi", text: "Read your assistants' instructions and tools with an API key." }] : []),
  ];
}

const ACTION: Record<Source, string> = {
  agent: "Start Claude Code or Codex in your repository",
  paste: "Paste or upload each agent's instructions",
  sopc: "Bring in your sopc project",
  elevenlabs: "Read your agents from ElevenLabs",
  vapi: "Read your assistants from Vapi",
};

export function ImportStart() {
  const { ws } = useWs();
  const plats = usePlatforms();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [where, setWhere] = useState<Where | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  const imports = useQuery(iq.list(ws));
  const open = (imports.data ?? []).filter((i) => !["applied", "discarded"].includes(i.status));
  const routes = where ? sources(plats, where) : [];
  const chosen = routes.find((r) => r.value === source) ?? null;
  const pickWhere = (v: Where) => {
    if (v !== where) setSource(null);
    setWhere(v);
    setStep(2);
  };
  const pickSource = (v: Source) => {
    setSource(v);
    setStep(3);
  };
  return (
    <Page>
      <PageTitle>Import agents</PageTitle>
      <Sub>
        Bring in the prompts your agents use today. sopops finds reusable instructions and workflows, while preserving what each prompt says. Nothing changes until you review it.
      </Sub>
      {open.length > 0 && (
        <Panel title="Pick up an import you started">
          <ul className="grid gap-1">
            {open.map((i) => (
              <li key={i.id} className="flex flex-wrap items-center gap-2">
                <HrefLink href={wsHref(ws, "import", i.id)}>
                  {i.agents.length ? i.agents.join(", ") : i.source === "agent" ? "Claude Code / Codex" : i.source === "sopc" ? "sopc project" : "Import"}
                </HrefLink>
                <Tag>{i.status}</Tag>
                <Note>{when(i.created_at)}</Note>
              </li>
            ))}
          </ul>
        </Panel>
      )}
      <Step
        n={1}
        title="Where should the instructions live?"
        state={step === 1 ? "open" : "done"}
        answer={WHERE.find((w) => w.value === where)?.title}
        onChange={() => setStep(1)}
      >
        <Choices label="Where the instructions live" options={WHERE} value={where} onPick={pickWhere} />
      </Step>
      <Step n={2} title="Where are they now?" state={step === 2 ? "open" : step === 1 ? "waiting" : "done"} answer={chosen?.title} onChange={() => setStep(2)}>
        <Choices label="Where the instructions are now" options={routes} value={source} onPick={pickSource} />
      </Step>
      <Step n={3} title={chosen ? ACTION[chosen.value] : "Bring them in"} state={step === 3 && chosen ? "open" : "waiting"}>
        {chosen?.value === "agent" ? (
          <AgentSource mode={where === "repo" ? "repo" : "hosted"} />
        ) : (
          <>
            {where === "repo" && <RepoPanel />}
            {chosen?.value === "paste" ? <PasteSource /> : chosen?.value === "sopc" ? <SopcSource /> : chosen ? <PlatformSource key={chosen.value} platform={chosen.value} /> : null}
          </>
        )}
      </Step>
    </Page>
  );
}

/** One step: its question on the header band, then the answers, or the answer given with a way back to it. */
function Step({
  n,
  title,
  state,
  answer,
  onChange,
  children,
}: {
  n: number;
  title: string;
  state: "open" | "done" | "waiting";
  answer?: string;
  onChange?: () => void;
  children: ReactNode;
}) {
  return (
    <section className={cn("bg-card mb-4 border", state === "waiting" && "opacity-60")}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b bg-[var(--band)] px-4 py-2">
        <h2 className="t-lead">
          <span className="text-muted-foreground mr-2">{n}</span>
          {title}
        </h2>
        {state === "done" && answer && (
          <>
            <span className="flex-1" />
            <Note>{answer}</Note>
            <Button variant="link" size="xs" className="h-auto px-0" onClick={onChange}>
              Change
            </Button>
          </>
        )}
      </div>
      {state === "open" && <div className="px-4 py-4">{children}</div>}
    </section>
  );
}

/** The answers to one question, as cards. Picking one opens the next step. */
function Choices<T extends string>({ label, options, value, onPick }: { label: string; options: Choice<T>[]; value: T | null; onPick: (v: T) => void }) {
  return (
    <div role="radiogroup" aria-label={label} className="grid gap-2 sm:grid-cols-2">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          aria-disabled={o.disabled || undefined}
          disabled={o.disabled}
          onClick={() => onPick(o.value)}
          className={cn("bg-card cursor-pointer border px-4 py-3 text-left hover:bg-muted", value === o.value && "border-primary bg-brand-soft hover:bg-brand-soft", o.disabled && "cursor-not-allowed opacity-60 hover:bg-card")}
        >
          <div className="flex items-center gap-2 font-semibold">
            {o.icon ?? <PlatformIcon platform={o.value} size={14} />}
            {o.title}
          </div>
          <div className="text-muted-foreground text-sm">{o.text}</div>
          {o.note && <div className="text-muted-foreground mt-1 text-xs">{o.note}</div>}
        </button>
      ))}
    </div>
  );
}

/** The repository the instructions are to live in. Only ever shown after that answer. */
function RepoPanel({ children }: { children?: ReactNode }) {
  return (
    <Panel title="Your GitHub repository">
      <div className="grid gap-3">
        <Note>Fixes arrive as pull requests on this repository. GitHub support is in alpha.</Note>
        <RepoConnect />
        {children}
      </div>
    </Panel>
  );
}

function NeedsModel({ children }: { children: ReactNode }) {
  const { ws } = useWs();
  const caps = useQuery(iq.capabilities(ws));
  if (caps.data && !caps.data.llm)
    return (
      <Banner tone="warn">
        sopops has no model to import with yet. Add your AI keys in <HrefLink href={settingsHref(ws, "checks")}>Settings, under Checks</HrefLink>, or use Claude Code or Codex,
        which use your own agent.
      </Banner>
    );
  return <>{children}</>;
}

function useStart() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = async (body: Parameters<typeof sdk.createImport>[0]["body"]) => {
    setBusy(true);
    setError(null);
    try {
      const created = await call(sdk.createImport({ path: { workspace: ws }, body }));
      await qc.invalidateQueries({ queryKey: ["ws", ws, "imports"] });
      go(wsHref(ws, "import", created.id));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return { run, busy, error };
}

// --- paste or upload ---------------------------------------------------------------------

function PasteSource() {
  const plats = usePlatforms();
  const options = IMPORT_PLATFORMS.filter((p) => plats.has(p.value));
  const [rows, setRows] = useState<Row[]>(() => [blank(plats.first)]);
  const fileRef = useRef<HTMLInputElement>(null);
  const { run, busy, error } = useStart();
  const set = (i: number, r: Partial<Row>) => setRows(rows.map((x, j) => (j === i ? { ...x, ...r } : x)));
  const addFiles = async (files: FileList | null) => {
    if (!files) return;
    const read = await readPromptFiles(files);
    const kept = rows.filter((r) => r.prompt.trim() || r.name.trim());
    setRows([...kept, ...read.map((f) => ({ ...blank(plats.first), name: f.name, platform_id: f.name, prompt: f.prompt }))]);
  };
  const ready = rows.filter((r) => r.name.trim() && r.prompt.trim());
  return (
    <NeedsModel>
      <div
        className="grid gap-4"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          addFiles(e.dataTransfer.files);
        }}
      >
        <Note>One agent per box, with its full instructions exactly as its model gets them.</Note>
        {rows.map((r, i) => (
          <Panel key={i} className="mb-0">
            <div className="mb-3 grid gap-2 md:grid-cols-[1fr_150px_1fr_auto]">
              <Input aria-label={`Agent ${i + 1} name`} placeholder="Agent name, e.g. tonys-pizza" value={r.name} onChange={(e) => set(i, { name: e.target.value })} />
              <Select value={r.platform} onValueChange={(v) => set(i, { platform: v as Platform })}>
                <SelectTrigger aria-label={`Agent ${i + 1} platform`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {options.map((p) => (
                    <SelectItem key={p.value} value={p.value}>
                      <PlatformLabel platform={p.value} />
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                aria-label={`Agent ${i + 1} platform id`}
                placeholder={`${IMPORT_PLATFORMS.find((p) => p.value === r.platform)?.idLabel} (optional)`}
                value={r.platform_id}
                onChange={(e) => set(i, { platform_id: e.target.value })}
              />
              <Button variant="ghost" size="icon-sm" aria-label={`Remove agent ${i + 1}`} disabled={rows.length === 1} onClick={() => setRows(rows.filter((_, j) => j !== i))}>
                <X />
              </Button>
            </div>
            <Textarea
              aria-label={`Agent ${i + 1} instructions`}
              className="min-h-40 font-mono text-sm"
              placeholder="The agent's full instructions, exactly as the model gets them"
              value={r.prompt}
              onChange={(e) => set(i, { prompt: e.target.value })}
            />
            <Input
              className="mt-2"
              aria-label={`Agent ${i + 1} tools`}
              placeholder="Tool names it can call, separated by commas (optional): lookup_allergens, transfer_to_staff"
              value={r.tools}
              onChange={(e) => set(i, { tools: e.target.value })}
            />
          </Panel>
        ))}
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={() => setRows([...rows, blank(plats.first)])}>
            + Add agent
          </Button>
          <Button variant="outline" onClick={() => fileRef.current?.click()}>
            <Upload /> Upload .txt or .md files
          </Button>
          <input ref={fileRef} type="file" multiple accept=".txt,.md,.markdown,text/plain,text/markdown" className="hidden" onChange={(e) => addFiles(e.target.files)} />
          <Note>Or drop files here. Each file is one agent, named after the file.</Note>
        </div>
        {error ? <ErrorBanner error={error} lead="The import can't start:" /> : null}
        <div>
          <Button
            disabled={busy || !ready.length}
            onClick={() =>
              run({
                author: author(),
                sources: ready.map((r) => ({
                  name: r.name.trim(),
                  platform: r.platform,
                  platform_id: r.platform_id.trim() || null,
                  prompt: r.prompt,
                  tools: r.tools.split(",").map((t) => t.trim()).filter(Boolean),
                })),
              })
            }
          >
            {busy ? "Starting…" : `Import ${plural(ready.length, "agent", "agents")}`}
          </Button>
        </div>
      </div>
    </NeedsModel>
  );
}

// --- ElevenLabs and Vapi ------------------------------------------------------------------------

function PlatformSource({ platform }: { platform: "elevenlabs" | "vapi" }) {
  const label = platform === "elevenlabs" ? "ElevenLabs" : "Vapi";
  const [key, setKey] = useState("");
  const [agents, setAgents] = useState<{ id: string; name: string }[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [listing, setListing] = useState(false);
  const [listError, setListError] = useState<unknown>(null);
  const { run, busy, error } = useStart();
  const list = async () => {
    setListing(true);
    setListError(null);
    try {
      const found = await call(sdk.listPlatformAgents({ body: { platform, api_key: key } }));
      setAgents(found);
      setPicked(new Set(found.map((a) => a.id)));
    } catch (e) {
      setListError(e);
    } finally {
      setListing(false);
    }
  };
  return (
    <NeedsModel>
      <div className="grid gap-4">
        <div>
          <label htmlFor="f-key" className="mb-0.5 block font-semibold">
            {label} API key
          </label>
          <p className="text-muted-foreground mb-2 max-w-[72ch] text-sm">
            sopops reads your agents' instructions and tool names with this key, then forgets it. It isn't stored or logged, and a read-only key is enough.
          </p>
          <div className="flex flex-wrap gap-2">
            <Input
              id="f-key"
              type="password"
              autoComplete="off"
              className="max-w-md"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={platform === "vapi" ? "Private key" : "xi-api-key"}
            />
            <Button variant="outline" disabled={!key.trim() || listing} onClick={list}>
              {listing ? "Reading…" : "List agents"}
            </Button>
          </div>
        </div>
        {listError ? <ErrorBanner error={listError} lead={`${label} didn't list the agents:`} /> : null}
        {agents && (
          <Panel title={`Agents on ${label}`}>
            {agents.length ? (
              <ul className="grid gap-2">
                {agents.map((a) => (
                  <li key={a.id}>
                    <label className="flex cursor-pointer items-center gap-2">
                      <Checkbox
                        checked={picked.has(a.id)}
                        onCheckedChange={(c) => {
                          const next = new Set(picked);
                          if (c === true) next.add(a.id);
                          else next.delete(a.id);
                          setPicked(next);
                        }}
                      />
                      <span className="font-medium">{a.name}</span> <Note>{a.id}</Note>
                    </label>
                  </li>
                ))}
              </ul>
            ) : (
              <Note>The key can't see any agents.</Note>
            )}
          </Panel>
        )}
        {error ? <ErrorBanner error={error} lead="The import can't start:" /> : null}
        {agents && (
          <div>
            <Button disabled={busy || !picked.size} onClick={() => run({ author: author(), platform: { platform, api_key: key, agent_ids: [...picked] } })}>
              {busy ? "Reading instructions…" : `Import ${plural(picked.size, "agent", "agents")}`}
            </Button>
          </div>
        )}
      </div>
    </NeedsModel>
  );
}

// --- Claude Code or Codex -------------------------------------------------------------------------

function AgentSource({ mode }: { mode: "hosted" | "repo" }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const repo = useQuery(repoQuery(ws));
  const project = useQuery({ ...repoProjectQuery(ws), enabled: !!repo.data });
  const [useAgent, setUseAgent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const state = repo.data ? project.data?.state : undefined;
  // Repo mode: the coding agent runs when there's no project to import (it proposes one), or to rewrite one sopc can't build.
  const agentReady = mode === "hosted" || state === "missing" || (state === "invalid" && useAgent);
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const k = await call(sdk.startAgentImport({ path: { workspace: ws }, body: { mode, server_url: apiOrigin(), author: author() } }));
      rememberKickoff(k);
      await qc.invalidateQueries({ queryKey: ["ws", ws, "imports"] });
      go(wsHref(ws, "import", k.import.id));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid gap-4">
      <p className="max-w-[75ch]">
        You run one command in your agents' repository. Claude Code or Codex finds every agent's instructions, extracts them with the sopops tools, checks nothing was
        lost, and sends them here, where you watch it work and review the result.
      </p>
      {mode === "repo" && <RepoPanel>{repo.data && <ProjectFinding repo={repo.data} useAgent={useAgent} onAgent={() => setUseAgent(true)} />}</RepoPanel>}
      {error ? <ErrorBanner error={error} lead="Couldn't create the token:" /> : null}
      {agentReady && (
        <div className="grid justify-items-start gap-2">
          <Button disabled={busy} onClick={start}>
            {busy ? "Creating…" : "Create token and commands"}
          </Button>
          <Note>The token works for 2 hours, only for importing into your team. It can't publish anything.</Note>
        </div>
      )}
    </div>
  );
}
