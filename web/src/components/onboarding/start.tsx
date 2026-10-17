// Import agents, step 1: choose where the prompts come from, then start the import.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload, X } from "lucide-react";
import { type ReactNode, useRef, useState } from "react";
import { call, sdk } from "@/lib/api";
import { PLATFORMS, plural, when } from "@/lib/kinds";
import { iq, readPromptFiles, rememberKickoff } from "@/lib/onboarding";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ErrorBanner, Note, PageTitle, Panel, Segmented, Sub, Tag } from "../bits";
import { author } from "../form";
import { HrefLink, useGo } from "../href-link";
import { Page } from "../item-shell";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Input } from "../ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";
import { Textarea } from "../ui/textarea";

type Source = "agent" | "paste" | "elevenlabs" | "vapi";
type Platform = "livekit" | "elevenlabs" | "vapi";
type Row = { name: string; platform: Platform; platform_id: string; tools: string; prompt: string };

const IMPORT_PLATFORMS = PLATFORMS.filter((p) => p.value !== "retell");
const blank = (): Row => ({ name: "", platform: "livekit", platform_id: "", tools: "", prompt: "" });

const SOURCES: { value: Source; title: string; text: string }[] = [
  { value: "agent", title: "Claude Code or Codex", text: "Recommended. Your coding agent finds the instructions wherever they live (code, config, a database) and sends them here." },
  { value: "paste", title: "Paste or upload", text: "LiveKit prompts from your code, or any prompt as text. One agent per prompt or file." },
  { value: "elevenlabs", title: "ElevenLabs", text: "Read your agents' prompts and tools with an API key." },
  { value: "vapi", title: "Vapi", text: "Read your assistants' prompts and tools with an API key." },
];

export function ImportStart() {
  const { ws } = useWs();
  const [source, setSource] = useState<Source>("agent");
  const imports = useQuery(iq.list(ws));
  const open = (imports.data ?? []).filter((i) => !["applied", "discarded"].includes(i.status));
  return (
    <Page>
      <PageTitle>Import agents</PageTitle>
      <Sub>
        Bring in the prompts your agents run today. sopops splits each one into the agent's own text, shared instructions and procedures, merges what agents share, and
        shows where they differ. You review everything before it's saved, as drafts.
      </Sub>
      {open.length > 0 && (
        <Panel title="In progress">
          <ul className="grid gap-1">
            {open.map((i) => (
              <li key={i.id} className="flex flex-wrap items-center gap-2">
                <HrefLink href={wsHref(ws, "import", i.id)}>{i.agents.length ? i.agents.join(", ") : i.source === "agent" ? "Claude Code / Codex" : "Import"}</HrefLink>
                <Tag>{i.status}</Tag>
                <Note>{when(i.created_at)}</Note>
              </li>
            ))}
          </ul>
        </Panel>
      )}
      <div role="radiogroup" aria-label="Where the prompts are" className="mb-6 grid gap-2.5 sm:grid-cols-2">
        {SOURCES.map((s) => (
          <button
            key={s.value}
            type="button"
            role="radio"
            aria-checked={source === s.value}
            onClick={() => setSource(s.value)}
            className={cn("bg-card cursor-pointer rounded-xl border px-4 py-3 text-left hover:bg-muted", source === s.value && "border-primary bg-brand-soft hover:bg-brand-soft")}
          >
            <div className="font-semibold">{s.title}</div>
            <div className="text-muted-foreground text-[13px]">{s.text}</div>
          </button>
        ))}
      </div>
      {source === "agent" ? <AgentSource /> : source === "paste" ? <PasteSource /> : <PlatformSource key={source} platform={source} />}
    </Page>
  );
}

function NeedsModel({ children }: { children: ReactNode }) {
  const caps = useQuery(iq.capabilities());
  if (caps.data && !caps.data.llm)
    return (
      <Banner tone="warn">
        sopops has no model for extraction yet. Set <code>SOPOPS_MODEL_REASONING</code> (and its provider key) where sopops runs, or use Claude Code or Codex, which use your
        own agent.
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
  const [rows, setRows] = useState<Row[]>([blank()]);
  const fileRef = useRef<HTMLInputElement>(null);
  const { run, busy, error } = useStart();
  const set = (i: number, r: Partial<Row>) => setRows(rows.map((x, j) => (j === i ? { ...x, ...r } : x)));
  const addFiles = async (files: FileList | null) => {
    if (!files) return;
    const read = await readPromptFiles(files);
    const kept = rows.filter((r) => r.prompt.trim() || r.name.trim());
    setRows([...kept, ...read.map((f) => ({ ...blank(), name: f.name, platform_id: f.name, prompt: f.prompt }))]);
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
        {rows.map((r, i) => (
          <Panel key={i} className="mb-0">
            <div className="mb-3 grid gap-2.5 md:grid-cols-[1fr_150px_1fr_auto]">
              <Input aria-label={`Agent ${i + 1} name`} placeholder="Agent name, e.g. tonys-pizza" value={r.name} onChange={(e) => set(i, { name: e.target.value })} />
              <Select value={r.platform} onValueChange={(v) => set(i, { platform: v as Platform })}>
                <SelectTrigger aria-label={`Agent ${i + 1} platform`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {IMPORT_PLATFORMS.map((p) => (
                    <SelectItem key={p.value} value={p.value}>
                      {p.label}
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
              aria-label={`Agent ${i + 1} prompt`}
              className="min-h-40 font-mono text-[13px]"
              placeholder="The agent's full prompt, exactly as the model gets it"
              value={r.prompt}
              onChange={(e) => set(i, { prompt: e.target.value })}
            />
            <Input
              className="mt-2.5"
              aria-label={`Agent ${i + 1} tools`}
              placeholder="Tool names it can call, separated by commas (optional): lookup_allergens, transfer_to_staff"
              value={r.tools}
              onChange={(e) => set(i, { tools: e.target.value })}
            />
          </Panel>
        ))}
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={() => setRows([...rows, blank()])}>
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
          <p className="text-muted-foreground mb-2 max-w-[72ch] text-[13px]">
            Used for this import only: sopops reads your agents' prompts and tool names with it, then forgets it. It isn't stored or logged. A read-only key is enough.
          </p>
          <div className="flex flex-wrap gap-2">
            <Input id="f-key" type="password" autoComplete="off" className="max-w-md" value={key} onChange={(e) => setKey(e.target.value)} placeholder={platform === "vapi" ? "Private key" : "xi-api-key"} />
            <Button variant="outline" disabled={!key.trim() || listing} onClick={list}>
              {listing ? "Reading…" : "List agents"}
            </Button>
          </div>
        </div>
        {listError ? <ErrorBanner error={listError} lead={`${label} didn't list the agents:`} /> : null}
        {agents && (
          <Panel title={`Agents on ${label}`}>
            {agents.length ? (
              <ul className="grid gap-1.5">
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
              {busy ? "Reading prompts…" : `Import ${plural(picked.size, "agent", "agents")}`}
            </Button>
          </div>
        )}
      </div>
    </NeedsModel>
  );
}

// --- Claude Code or Codex -------------------------------------------------------------------------

function AgentSource() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const [mode, setMode] = useState<"hosted" | "repo">("hosted");
  const [repo, setRepo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const k = await call(sdk.startAgentImport({ path: { workspace: ws }, body: { mode, repo: mode === "repo" ? repo.trim() || null : null, server_url: window.location.origin, author: author() } }));
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
        You start Claude Code or Codex in your agents' repo with one command and one prompt. It finds every agent's instructions, extracts them with the sopops tools, checks
        nothing was lost, and sends them here, where you watch it work and review the result.
      </p>
      <div>
        <div className="mb-1.5 font-semibold">Where should the instructions live?</div>
        <Segmented
          label="Where the instructions live"
          value={mode}
          onChange={setMode}
          options={[
            ["hosted", "In sopops"],
            ["repo", "In my repo too"],
          ]}
        />
        <p className="text-muted-foreground mt-2 max-w-[72ch] text-[13px]">
          {mode === "hosted"
            ? "Instructions are edited and published in sopops. Your repo doesn't change."
            : "The agent also opens a pull request adding a sopc project at sops/. sopops will sync from it once GitHub sync is available; for now the repo is only recorded."}
        </p>
      </div>
      {mode === "repo" && (
        <div>
          <label htmlFor="f-repo" className="mb-1 block font-semibold">
            Repository
          </label>
          <Input id="f-repo" className="max-w-md" placeholder="github.com/your-org/agents" value={repo} onChange={(e) => setRepo(e.target.value)} />
        </div>
      )}
      {error ? <ErrorBanner error={error} lead="Couldn't create the token:" /> : null}
      <div>
        <Button disabled={busy || (mode === "repo" && !repo.trim())} onClick={start}>
          {busy ? "Creating…" : "Create token and commands"}
        </Button>
        <Note className="ml-3">The token works for 2 hours, only for importing into {ws}. It can't publish anything.</Note>
      </div>
    </div>
  );
}
