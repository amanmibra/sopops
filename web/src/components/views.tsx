// The pages that aren't forms: lists, overview, instructions, unpublished changes, releases, convert, home.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ComponentProps, type ReactNode, useState } from "react";
import { toast } from "sonner";
import { type Component, type DraftAgent, type DraftItem, type ReleaseDetail, call, q, refresh, sdk } from "@/lib/api";
import { textCount } from "@/lib/diff";
import { KIND, type Kind, type ListKind, itemOfPath, kindOfComponent, platformName, platformRefName, plural, when } from "@/lib/kinds";
import type { Item } from "@/lib/model";
import { settingsHref, useWs, wsHref } from "@/lib/workspace";
import { Banner, ConfirmButton, Counts, ErrorBanner, IssueList, LintList, Note, PageTitle, Panel, Segmented, Sub, Tag } from "./bits";
import { ChangeList, useFieldChanges } from "./change-list";
import { DiffView } from "./diff-view";
import { author } from "./form";
import { HrefLink, useGo } from "./href-link";
import { ItemHeader, Missing, Page } from "./item-shell";
import { PublishDialog, UnsavedBanner } from "./publish";
import { PushResultsPanel } from "./runtime";
import { Button } from "./ui/button";

// --- lists ---------------------------------------------------------------------------

/** The Agents page's actions. Which agent uses which block is on Overview (/overview). */
function AgentsActions() {
  const { ws, href } = useWs();
  const go = useGo();
  return (
    <>
      <span className="flex-1" />
      <Button variant="outline" onClick={() => go(settingsHref(ws, "import"))}>
        Add agents
      </Button>
      <Button onClick={() => go(href("agent", "new"))}>New agent</Button>
    </>
  );
}

const INTRO: Record<ListKind, string> = {
  agent: "One per voice agent. An agent's full instructions are its own text, then the blocks it lists, in that order.",
  instruction: "Text several agents get, such as identity, voice and policies. Edit it once and every agent that lists it changes.",
  procedure: "Step-by-step procedures the agents follow, with things they must never do and signs that a call needs a person.",
  group: "A named, ordered set of blocks that several agents share. An agent lists a group like any block, and it expands in place.",
};

export function ListView({ kind }: { kind: ListKind }) {
  const { model, href, changeOf } = useWs();
  const go = useGo();
  const items = [...model.list(kind)].sort((a, b) => ("name" in a ? a.name : a.id).localeCompare("name" in b ? b.name : b.id));
  const users = (it: Item) => new Set(("used_by" in it ? it.used_by : []).map((u) => u.agent)).size;
  const a = (it: Item) => it as never as Record<string, never>;
  const cols: Record<ListKind, [string, (it: Item) => ReactNode][]> = {
    agent: [
      ["Agent", (it) => <b>{it.id}</b>],
      ["Platform", (it) => `${platformName(a(it).platform)}: ${a(it).platform_id}`],
      ["Blocks", (it) => (a(it).blocks as string[]).map(model.blockName).join(", ") || "None"],
    ],
    instruction: [
      ["Name", (it) => <b>{it.id}</b>],
      ["Used by", (it) => plural(users(it), "agent", "agents")],
    ],
    procedure: [
      ["Name", (it) => <b>{a(it).name}</b>],
      ["Used by", (it) => plural(users(it), "agent", "agents")],
      ["Steps", (it) => (a(it).steps as unknown[]).length],
    ],
    group: [
      ["Name", (it) => <b>{it.id}</b>],
      ["Blocks", (it) => (a(it).blocks as string[]).map(model.blockName).join(", ") || "None"],
      ["Used by", (it) => plural(users(it), "agent", "agents")],
    ],
  };
  return (
    <Page>
      <PageTitle
        aside={
          kind === "agent" ? (
            <AgentsActions />
          ) : (
            <>
              <span className="flex-1" />
              <Button onClick={() => go(href(kind, "new"))}>New {KIND[kind].one.toLowerCase()}</Button>
            </>
          )
        }
      >
        {KIND[kind].many}
      </PageTitle>
      <Sub>{INTRO[kind]}</Sub>
      {items.length ? (
        <table className="w-full border-collapse">
          <thead>
            <tr>
              {cols[kind].map(([h]) => (
                <th key={h} className="text-muted-foreground border-b px-2 py-2 text-left text-sm font-semibold">
                  {h}
                </th>
              ))}
              <th className="border-b" />
            </tr>
          </thead>
          <tbody>
            {items.map((it) => {
              const c = changeOf(kind, it.id);
              return (
                <tr key={it.id} className="cursor-pointer hover:bg-muted" onClick={() => go(href(kind, it.id))}>
                  {cols[kind].map(([h, fn]) => (
                    <td key={h} className="border-b px-2 py-2 align-top">
                      {fn(it)}
                    </td>
                  ))}
                  <td className="border-b px-2 py-2 text-right">{c && <Tag tone={c.change}>{c.change}</Tag>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <Note>No {KIND[kind].many.toLowerCase()} yet.</Note>
      )}
    </Page>
  );
}

// --- an agent's instructions: draft and live --------------------------------------------

function Parts({ components }: { components: Component[] }) {
  const { href, model } = useWs();
  return (
    <div className="flex flex-wrap gap-2" aria-label="Built from">
      {components.map((c, i) => {
        const kind = kindOfComponent(c.kind);
        return (
          <HrefLink
            key={i}
            href={href(kind, c.id)}
            title={`${KIND[kind].one}, version ${c.version ?? "?"}`}
            className="bg-card text-foreground rounded-full border px-2 py-px text-xs hover:no-underline hover:bg-muted"
          >
            {c.kind === "agent" ? "Agent setup" : model.itemName(kind, c.id)} v{c.version ?? "?"}
          </HrefLink>
        );
      })}
    </div>
  );
}

function PromptBox({ title, aside, components, prompt, empty }: { title: string; aside?: ReactNode; components?: Component[]; prompt: string | null; empty?: string }) {
  return (
    <section className="bg-card min-w-0 overflow-hidden rounded-xl border">
      <header className="flex flex-wrap items-center gap-2 border-b bg-[var(--band)] px-4 py-2">
        <b className="mr-auto">{title}</b>
        {aside}
      </header>
      {prompt !== null ? (
        <>
          {components && (
            <div className="bg-muted border-b px-4 py-2">
              <Parts components={components} />
            </div>
          )}
          <pre className="prompt max-h-[70vh]">{prompt}</pre>
        </>
      ) : (
        <pre className="prompt text-muted-foreground">{empty}</pre>
      )}
    </section>
  );
}

export function PromptView({ id }: { id: string }) {
  const { ws, cfg, draft: d } = useWs();
  const [showing, setShowing] = useState<"changes" | "full">("changes");
  const rel = useQuery({ ...q.release(ws, d.release ?? 0), enabled: !!d.release });
  if (!cfg.agents.some((a) => a.id === id)) return <Missing kind="agent" id={id} />;
  const head = <ItemHeader kind="agent" id={id} title={id} tab="prompt" />;
  if (d.release && rel.isPending) return <Page wide>{head}<p className="text-muted-foreground">Loading…</p></Page>;
  if (rel.isError) return <Page wide>{head}<ErrorBanner error={rel.error} /></Page>;
  const draft = d.agents.find((a) => a.agent === id);
  const liveAgent = rel.data?.agents[id];
  const live = liveAgent ? { ...liveAgent, release: rel.data!.number } : null;
  const draftPrompt = draft?.prompt ?? null;
  const livePrompt = live?.prompt ?? null;
  const hash = (h?: string | null) => (h ? <Note>hash {h.slice(0, 10)}</Note> : null);
  // Nothing to compare (same as live, or never published): just the prompt.
  if (draftPrompt !== null && (livePrompt === null || livePrompt === draftPrompt)) {
    const why = livePrompt === null ? "Not published yet" : `Same as live (release #${live!.release})`;
    return (
      <Page wide>
        {head}
        <PromptBox
          title={livePrompt === null ? "Draft" : "Instructions"}
          aside={
            <>
              <Note>{why}</Note>
              {hash(draft!.hash)}
            </>
          }
          components={draft!.components}
          prompt={draftPrompt}
        />
      </Page>
    );
  }
  const status = draft?.status ?? "unchanged";
  const sub = !d.valid
    ? "The draft has errors, so it can't be built. See Unpublished changes."
    : status === "unchanged"
      ? "The draft gives this agent the same instructions as the live release."
      : status === "added"
        ? "This agent isn't live yet. It gets these instructions once you publish."
        : "The draft changes this agent's instructions. The agent gets it once you publish.";
  return (
    <Page wide>
      {head}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Sub className="m-0">{sub}</Sub>
        <span className="flex-1" />
        {status !== "unchanged" && <Counts {...textCount(livePrompt, draftPrompt)} />}
        <Segmented
          label="Show"
          value={showing}
          options={[
            ["changes", "What changes"],
            ["full", "Full instructions"],
          ]}
          onChange={setShowing}
        />
      </div>
      {showing === "changes" ? (
        <div>
          <div className="mb-2 grid gap-2">
            {live && (
              <div className="flex flex-wrap items-center gap-2">
                <Note>Live, release #{live.release}:</Note>
                <Parts components={live.components} />
              </div>
            )}
            {draftPrompt !== null && (
              <div className="flex flex-wrap items-center gap-2">
                <Note>Draft:</Note>
                <Parts components={draft!.components} />
              </div>
            )}
          </div>
          <DiffView before={livePrompt} after={draftPrompt} empty="The draft instructions are the same as the live ones." />
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <PromptBox title="Draft" aside={hash(draft?.hash)} components={draft?.components} prompt={draftPrompt} empty="Not in the draft." />
          <PromptBox title={live ? `Live (release #${live.release})` : "Live"} aside={hash(live?.hash)} components={live?.components} prompt={livePrompt} empty="Not live yet." />
        </div>
      )}
    </Page>
  );
}

// --- unpublished changes ---------------------------------------------------------------

export function Collapsible({ open: initial, summary, children }: { open: boolean; summary: ReactNode; children: ReactNode }) {
  return (
    <details open={initial} className="group bg-card mb-2 rounded-xl border">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-4 py-2 [&::-webkit-details-marker]:hidden">
        <span className="text-muted-foreground transition-transform group-open:rotate-90">▸</span>
        {summary}
      </summary>
      <div className="px-4 pb-3.5">{children}</div>
    </details>
  );
}

const CHANGE_LABEL = { added: "New", edited: "Edited", deleted: "Deleted" };

export function ChangedItem({ i, open = true }: { i: DraftItem; open?: boolean }) {
  const { href, model } = useWs();
  const fieldChanges = useFieldChanges();
  let body: ReactNode;
  if (i.change === "edited" && i.before && i.after) body = <ChangeList kind={i.kind} before={i.before} after={i.after} />;
  else if (i.change === "edited" && i.after)
    body = (
      <>
        <Note>The released version can't be read field by field, so here is the draft.</Note>
        <pre className="plain mt-2">{model.readable(i.kind, i.after).join("\n")}</pre>
      </>
    );
  else if (i.change === "added" && i.after) body = <pre className="plain add">{model.readable(i.kind, i.after).join("\n")}</pre>;
  else if (i.change === "deleted" && i.before) body = <pre className="plain del">{model.readable(i.kind, i.before).join("\n")}</pre>;
  else body = <Note>The draft has errors, so the details can't be shown.</Note>;
  const n = i.change === "edited" && i.before && i.after ? fieldChanges(i.kind, i.before, i.after).length : 0;
  return (
    <Collapsible
      open={open}
      summary={
        <>
          <Tag tone={i.change}>{CHANGE_LABEL[i.change]}</Tag>
          <b>{i.name}</b>
          <span className="text-muted-foreground">{KIND[i.kind].one}</span>
          {n > 0 && <Note>{plural(n, "field", "fields")}</Note>}
          <span className="flex-1" />
          {i.change !== "deleted" && (
            <HrefLink href={href(i.kind, i.id)} className="text-sm" onClick={(e) => e.stopPropagation()}>
              Open
            </HrefLink>
          )}
        </>
      }
    >
      {body}
    </Collapsible>
  );
}

export function ChangedAgent({ a, open }: { a: DraftAgent; open: boolean }) {
  const { href } = useWs();
  return (
    <Collapsible
      open={open}
      summary={
        <>
          <Tag tone={a.status}>{{ added: "New agent", changed: "Instructions change", removed: "Removed", unchanged: "Unchanged" }[a.status]}</Tag>
          <b>{a.agent}</b>
          <span className="text-muted-foreground">{platformRefName(a.platform_ref)}</span>
          <Counts {...textCount(a.released_prompt, a.prompt)} />
          <span className="flex-1" />
          {a.status !== "removed" && (
            <HrefLink href={href("agent", a.agent, "prompt")} className="text-sm" onClick={(e) => e.stopPropagation()}>
              Full instructions
            </HrefLink>
          )}
        </>
      }
    >
      <DiffView before={a.released_prompt} after={a.prompt} />
    </Collapsible>
  );
}

export function ChangesView() {
  const { draft: d } = useWs();
  const [publishing, setPublishing] = useState(false);
  const changed = d.agents.filter((a) => a.status !== "unchanged");
  const errors = d.issues.filter((i) => i.severity === "error");
  const warnings = d.issues.filter((i) => i.severity === "warning");
  return (
    <Page>
      <PageTitle
        aside={
          <>
            {d.items.length > 0 && <Tag tone="changed" big>{plural(d.items.length, "change", "changes")}</Tag>}
            {changed.length > 0 && <Tag tone="changed" big>{plural(changed.length, "agent changes", "agents change")}</Tag>}
          </>
        }
      >
        Unpublished changes
      </PageTitle>
      <Sub>{d.release ? `Agents are on release #${d.release}. Saved edits wait here until you publish them.` : "Nothing is live yet. Publishing sends the first release to your agents."}</Sub>
      {errors.length > 0 && (
        <Banner tone="err">
          Fix these before publishing:
          <IssueList issues={errors} />
        </Banner>
      )}
      <UnsavedBanner lead="Open with edits that aren't saved as drafts (not listed here, and not published):" />
      {d.items.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 pb-2">
          <Button disabled={errors.length > 0} onClick={() => setPublishing(true)}>
            Publish…
          </Button>
          <Note>You'll see a summary and add a release note before anything goes live.</Note>
        </div>
      )}
      <PublishDialog open={publishing} onOpenChange={setPublishing} />
      <h2 className="mt-6 mb-2 text-base font-semibold">{d.items.length ? "What was edited" : "Nothing to publish"}</h2>
      {d.items.length ? d.items.map((i) => <ChangedItem key={`${i.kind}:${i.id}`} i={i} />) : <Note>Edits you save show up here, with what changed in each field and in each agent's instructions.</Note>}
      {d.items.length > 0 && (
        <>
          <h2 className="mt-6 mb-2 text-base font-semibold">
            {changed.length ? `How ${changed.length === 1 ? "the agent's instructions change" : "agents' instructions change"}` : "No agent's instructions change"}
          </h2>
          {changed.length ? changed.map((a, i) => <ChangedAgent key={a.agent} a={a} open={i < 3} />) : <Note>The saved edits don't change what any agent is told.</Note>}
        </>
      )}
      {warnings.length > 0 && (
        <Banner tone="warn" className="mt-4">
          Worth a look (these don't block publishing):
          <IssueList issues={warnings} />
        </Banner>
      )}
      {d.lint.length > 0 && (
        <Banner tone="warn" className="mt-4">
          Possible duplicates or conflicts, found by sopc lint (these don't block publishing; decide which text is right):
          <LintList findings={d.lint} />
        </Banner>
      )}
    </Page>
  );
}

// --- releases ----------------------------------------------------------------------

/** Makes a release live (a rollback when it's older), with the toast and refresh both release pages use. */
function useActivate() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [error, setError] = useState<unknown>(null);
  const activate = async (number: number) => {
    try {
      await call(sdk.activate({ path: { workspace: ws, number } }));
      toast.success(`Release #${number} is live. Agents get it on their next call.`);
      setError(null);
      await refresh(qc, ws);
    } catch (e) {
      setError(e);
    }
  };
  return { activate, error };
}

/** The two-click button that makes release `number` live. */
function ActivateButton({
  number,
  onConfirm,
  ...props
}: Omit<ComponentProps<typeof ConfirmButton>, "confirm" | "onConfirm"> & { number: number; onConfirm: (number: number) => void }) {
  return <ConfirmButton {...props} confirm={`Click again: agents get #${number} on their next call`} onConfirm={() => onConfirm(number)} />;
}

export function ReleasesView() {
  const { ws, releases } = useWs();
  const go = useGo();
  const { activate, error } = useActivate();
  const live = releases.find((r) => r.current)?.number;
  return (
    <Page>
      <PageTitle>Releases</PageTitle>
      <Sub>Each publish makes a release. Agents get the live release when a call starts. Roll back to an older one from its row, or open it to see what's in it first.</Sub>
      {error != null && <ErrorBanner error={error} />}
      {releases.length ? (
        <table className="w-full border-collapse">
          <thead>
            <tr>
              {["Release", "Note", "Published", ""].map((h) => (
                <th key={h} className="text-muted-foreground border-b px-2 py-2 text-left text-sm font-semibold">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {releases.map((r) => (
              <tr key={r.number} className="cursor-pointer hover:bg-muted" onClick={() => go(wsHref(ws, "releases", r.number))}>
                <td className="border-b px-2 py-2">
                  <b>#{r.number}</b>
                </td>
                <td className="border-b px-2 py-2">{r.note || <span className="text-muted-foreground">No note</span>}</td>
                <td className="text-muted-foreground border-b px-2 py-2 text-sm">
                  {when(r.created_at)}
                  {r.author ? `, ${r.author}` : ""}
                </td>
                <td className="border-b px-2 py-1 text-right" onClick={(e) => e.stopPropagation()}>
                  {r.current ? (
                    <Tag tone="live">live</Tag>
                  ) : (
                    <ActivateButton number={r.number} size="xs" variant="outline" onConfirm={(n) => void activate(n)}>
                      {live != null && r.number < live ? `Roll back to #${r.number}` : `Make #${r.number} live`}
                    </ActivateButton>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Note>Nothing published yet. Saved edits wait under Unpublished changes.</Note>
      )}
    </Page>
  );
}

export function ReleaseView({ number }: { number: number }) {
  const { ws, model } = useWs();
  const { activate, error } = useActivate();
  const rel = useQuery(q.release(ws, number));
  // `current` changes on activate, so read it from the release list rather than the cached detail
  const { releases } = useWs();
  if (rel.isPending) return <Page><p className="text-muted-foreground">Loading…</p></Page>;
  if (rel.isError) return <Page><ErrorBanner error={rel.error} /></Page>;
  const r: ReleaseDetail = rel.data;
  const current = releases.find((x) => x.number === number)?.current ?? r.current;
  const items = Object.entries(r.files)
    .map(([path, v]) => ({ where: itemOfPath(path), v }))
    .filter((x): x is { where: [Kind, string]; v: number } => !!x.where);
  const group = (kind: Kind) => items.filter((i) => i.where[0] === kind).map((i) => `${model.itemName(kind, i.where[1])} v${i.v}`).join(", ") || "None";
  return (
    <Page>
      <div className="mb-0.5 text-sm">
        <HrefLink href={wsHref(ws, "releases")} className="text-muted-foreground">
          Releases
        </HrefLink>
      </div>
      <PageTitle aside={current && <Tag tone="live">live</Tag>}>Release #{r.number}</PageTitle>
      <Sub>
        {r.note || "No note"}. Published {when(r.created_at)}
        {r.author ? ` by ${r.author}` : ""}.
      </Sub>
      {error != null && <ErrorBanner error={error} />}
      {!current && (
        <div className="mb-[18px]">
          <ActivateButton number={number} onConfirm={(n) => void activate(n)}>
            Make this release live
          </ActivateButton>
        </div>
      )}
      <Panel title="Agents">
        <table className="w-full border-collapse">
          <tbody>
            {Object.entries(r.agents).map(([id, a]) => (
              <tr key={id}>
                <td className="border-b px-2 py-2">
                  <b>{id}</b>
                </td>
                <td className="text-muted-foreground border-b px-2 py-2">{platformRefName(a.platform_ref)}</td>
                <td className="border-b px-2 py-2">
                  <Note>hash {a.hash.slice(0, 10)}</Note>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <PushResultsPanel number={number} live={current} />
      <Panel title="Built from">
        <table className="w-full border-collapse">
          <tbody>
            {(
              [
                ["Agents", "agent"],
                ["Shared instructions", "instruction"],
                ["Procedures", "procedure"],
                ["Settings and groups", "settings"],
              ] as [string, Kind][]
            ).map(([label, kind]) => (
              <tr key={kind}>
                <td className="text-muted-foreground w-[180px] border-b px-2 py-2 align-top">{label}</td>
                <td className="border-b px-2 py-2">{group(kind)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </Page>
  );
}

// --- converting an older sopc format ---------------------------------------------------

export function ConvertView() {
  const { ws, legacy } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const [error, setError] = useState<unknown>(null);
  const plan = useQuery({ queryKey: ["ws", ws, "migrate-plan"], queryFn: () => call(sdk.migrate({ path: { workspace: ws }, body: {} })) });
  const convert = async () => {
    try {
      await call(sdk.migrate({ path: { workspace: ws }, body: { apply: true, author: author(), note: "Converted with sopc migrate" } }));
      toast.success("Converted. The new files are a draft: check the changes, then publish.");
      await refresh(qc, ws);
      go(wsHref(ws, "changes"));
    } catch (e) {
      setError(e);
    }
  };
  return (
    <Page>
      <PageTitle>Convert your team's files</PageTitle>
      <Sub>
        {legacy === "removed_field"
          ? `Some shared instructions or procedures here are marked locked, a setting sopc no longer has. Converting runs sopc migrate, which removes it. No agent's instructions change.`
          : `Your team's files are stored in the format of sopc v0.0.8 and earlier, where shared instructions chose their agents. Now each agent lists the blocks it uses, in order. Converting runs sopc migrate: every agent's instructions stay the same, except that its own text moves to the top.`}
      </Sub>
      {plan.isPending ? (
        <p className="text-muted-foreground">Checking what converting changes…</p>
      ) : plan.isError ? (
        <ErrorBanner error={plan.error} lead="It can't be converted:" />
      ) : !plan.data.needed ? (
        <>
          <p>Nothing to convert.</p>
          <Button variant="outline" onClick={() => refresh(qc, ws)}>
            Reload
          </Button>
        </>
      ) : (
        <>
          <h2 className="mt-6 mb-2 text-base font-semibold">What changes</h2>
          <pre className="plain">{plan.data.plan}</pre>
          {error != null && <ErrorBanner error={error} lead="It can't be converted:" />}
          <div className="mt-6 flex flex-wrap items-center gap-2 border-t py-3">
            <ConfirmButton confirm="Click again to convert" onConfirm={convert}>
              Convert
            </ConfirmButton>
            <Note>The result is a draft. Nothing reaches agents until you publish.</Note>
          </div>
        </>
      )}
    </Page>
  );
}

// --- home ----------------------------------------------------------------------------

export function EmptyWorkspace() {
  const { ws, href } = useWs();
  const go = useGo();
  return (
    <div className="max-w-[60ch] py-10">
      <h1 className="mb-2 text-2xl font-semibold">Your team has no agents yet</h1>
      <p className="mb-4">
        Already have agents running? Import them: sopops splits each agent's instructions into shared instructions and procedures and shows where they differ. Or write the text several
        agents share as shared instructions, and each procedure they follow. Then build each agent (one per location or phone line) from those
        blocks, in the order its instructions should have them.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => go(settingsHref(ws, "import"))}>Import agents</Button>
        <Button variant="outline" onClick={() => go(href("instruction", "new"))}>
          New shared instruction
        </Button>
        <Button variant="outline" onClick={() => go(href("procedure", "new"))}>
          New procedure
        </Button>
        <Button variant="outline" onClick={() => go(href("agent", "new"))}>
          New agent
        </Button>
      </div>
    </div>
  );
}
