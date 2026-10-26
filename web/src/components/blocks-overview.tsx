// Overview: which agent uses which block, built for many agents. Agents are rows (they're what grows), blocks are
// narrow columns grouped by kind, each with how many agents use it. Agents whose blocks expand to the same set fold
// into one row, so with 50 stores on a handful of setups the differences stand out. Clicking a cell on an agent's row
// adds the block at the end of its list or takes it out, as a draft.
import { useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Search } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { toast } from "sonner";
import { call, issuesOf, refresh, sdk } from "@/lib/api";
import { KIND, type ListKind, plural } from "@/lib/kinds";
import type { Item } from "@/lib/model";
import { prefs } from "@/lib/prefs";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { Note, PageTitle, Segmented, Sub } from "./bits";
import { author } from "./form";
import { HrefLink, useGo } from "./href-link";
import { Page } from "./item-shell";
import { FilterSelect } from "./sessions";
import { Input } from "./ui/input";

type BlockKind = Exclude<ListKind, "agent">;
type Use = "direct" | { via: string } | null;
/** Past this many agents, agents with the same blocks start folded together. */
const FOLD_FROM = 12;
const KIND_TEXT: Record<BlockKind, string> = { group: "text-k-group", instruction: "text-k-instruction", procedure: "text-k-procedure" };

export function OverviewView() {
  const { ws, cfg, model, href } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const [busy, setBusy] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<BlockKind | undefined>();
  const [fold, setFold] = useState<"same" | "each">(() => (prefs.get(`overview.${ws}.fold`) as "same" | "each" | null) ?? (cfg.agents.length > FOLD_FROM ? "same" : "each"));
  const [open, setOpen] = useState<Set<string>>(new Set());

  const columns = useMemo(() => {
    const all: [BlockKind, Item[]][] = [
      ["group", cfg.groups],
      ["instruction", [...cfg.instructions].sort((a, b) => a.id.localeCompare(b.id))],
      ["procedure", [...cfg.procedures].sort((a, b) => a.name.localeCompare(b.name))],
    ];
    return all.filter(([k, items]) => items.length && (!kind || kind === k));
  }, [cfg, kind]);
  const blocks = columns.flatMap(([k, items]) => items.map((it) => ({ kind: k, id: it.id })));

  // What each agent gets: listed directly, or through a group (a group nested in a listed group counts as the outer one).
  const uses = useMemo(() => {
    const m = new Map<string, Map<string, Use>>();
    for (const a of cfg.agents) {
      const u = new Map<string, Use>();
      for (const b of a.blocks) u.set(b, "direct");
      for (const x of model.expand(a.blocks)) if (x.via && !u.has(x.id)) u.set(x.id, { via: x.via });
      for (const g of cfg.groups) {
        const via = g.used_by.find((v) => v.agent === a.id && v.via);
        if (via && !u.has(g.id)) u.set(g.id, { via: via.via! });
      }
      m.set(a.id, u);
    }
    return m;
  }, [cfg, model]);
  const usedBy = (block: string) => cfg.agents.filter((a) => uses.get(a.id)!.get(block)).length;

  const q = query.trim().toLowerCase();
  const agents = [...cfg.agents].sort((a, b) => a.id.localeCompare(b.id)).filter((a) => !q || a.id.toLowerCase().includes(q) || a.platform_id.toLowerCase().includes(q));
  // Agents whose blocks reach the same set, in the order of the largest set first.
  const rows = useMemo(() => {
    if (fold === "each") return agents.map((a) => ({ key: a.id, agents: [a.id] }));
    const by = new Map<string, string[]>();
    for (const a of agents) {
      const key = [...uses.get(a.id)!.keys()].sort().join("|");
      by.set(key, [...(by.get(key) ?? []), a.id]);
    }
    return [...by.entries()].sort((x, y) => y[1].length - x[1].length || x[1][0].localeCompare(y[1][0])).map(([key, ids]) => ({ key, agents: ids }));
  }, [agents, uses, fold]);

  const toggle = async (agent: string, id: string, use: Use) => {
    const name = model.blockName(id);
    if (use && use !== "direct") {
      toast.warning(`${agent} gets ${name} through the group ${use.via}.`, {
        description: `Take it out of that group, or open ${agent} and list the group's other blocks instead.`,
        action: { label: `Open ${agent}`, onClick: () => go(href("agent", agent)) },
      });
      return;
    }
    setBusy(`${agent}/${id}`);
    try {
      if (use) await call(sdk.removeBlock({ path: { workspace: ws, id: agent, block: id }, query: { author: author() } }));
      else await call(sdk.addBlock({ path: { workspace: ws, id: agent }, body: { block: id, author: author() } }));
      await refresh(qc, ws);
      toast.success(use ? `Took ${name} out of ${agent} (draft).` : `Added ${name} at the end of ${agent}'s blocks (draft).`);
    } catch (e) {
      toast.error(use ? `${name} can't be taken out of ${agent}` : `${name} can't be added to ${agent}`, { description: issuesOf(e).map((i) => i.text || i.message).join(" ") || (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  const cell = (agent: string, block: string) => {
    const use = uses.get(agent)!.get(block) ?? null;
    const nm = model.blockName(block);
    const label = use === "direct" ? `${agent} lists ${nm}. Click to take it out.` : use ? `${agent} gets ${nm} through ${use.via}.` : `Add ${nm} to ${agent}.`;
    return (
      <button
        type="button"
        aria-label={label}
        title={label}
        aria-pressed={use === "direct"}
        disabled={busy === `${agent}/${block}`}
        onClick={() => void toggle(agent, block, use)}
        className={cn("group text-primary hover:bg-muted flex size-full min-h-9 cursor-pointer items-center justify-center font-bold disabled:opacity-50", use === "direct" && "bg-brand-soft", use && use !== "direct" && "text-muted-foreground font-medium")}
      >
        {use ? "✓" : <span className="text-muted-foreground invisible group-hover:visible">+</span>}
      </button>
    );
  };
  /** A folded row's cell: every agent in it gets the block (they share one set), listed or through a group. */
  const foldedCell = (ids: string[], block: string) => {
    const us = ids.map((a) => uses.get(a)!.get(block) ?? null);
    if (!us[0]) return null;
    const direct = us.every((u) => u === "direct");
    const nm = model.blockName(block);
    return (
      <span title={direct ? `All ${ids.length} list ${nm}. Expand the row to edit one agent.` : `All ${ids.length} get ${nm}, some through a group.`} className={cn("flex min-h-9 items-center justify-center font-bold", direct ? "bg-brand-soft text-primary" : "text-muted-foreground font-medium")}>
        ✓
      </span>
    );
  };

  const setFoldPref = (v: "same" | "each") => {
    setFold(v);
    prefs.set(`overview.${ws}.fold`, v);
  };
  const shownAgents = rows.reduce((n, r) => n + r.agents.length, 0);
  return (
    <Page wide>
      <PageTitle>Overview</PageTitle>
      <Sub>
        Which agents use which blocks. Click a cell to add a block to an agent (it goes at the end of the agent's list) or take it out; each click saves a draft. How agents, blocks and
        groups fit together is in the <a href="https://sopops.dev/docs/instructions-model" target="_blank" rel="noreferrer" className="text-primary underline-offset-2 hover:underline">docs</a>.
      </Sub>
      {cfg.agents.length ? (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <label className="relative">
              <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find an agent" aria-label="Find an agent" className="h-8 w-56 pl-8" />
            </label>
            <FilterSelect<BlockKind> label="Block" value={kind} options={(["group", "instruction", "procedure"] as BlockKind[]).map((k) => [k, KIND[k].many])} onChange={setKind} />
            <span className="text-muted-foreground ml-auto text-sm tabular-nums">
              {shownAgents === cfg.agents.length ? plural(cfg.agents.length, "agent", "agents") : `${shownAgents} of ${cfg.agents.length} agents`}
              {fold === "same" && ` · ${plural(rows.length, "setup", "setups")}`}
            </span>
            <Segmented small label="Rows" value={fold} options={[["same", "Same blocks together"], ["each", "Every agent"]]} onChange={setFoldPref} />
          </div>
          <div className="bg-card max-h-[75vh] overflow-auto rounded-lg border">
            <table className="border-separate border-spacing-0">
              <thead>
                <tr>
                  <th rowSpan={2} className="bg-card sticky top-0 left-0 z-30 min-w-56 border-r border-b px-3 py-2 text-left align-bottom text-sm font-semibold">
                    Agent
                  </th>
                  {columns.map(([k, items]) => (
                    <th key={k} colSpan={items.length} scope="colgroup" className={cn("bg-card sticky top-0 z-20 h-8 border-b border-l px-2 text-left text-xs font-semibold whitespace-nowrap", KIND_TEXT[k])}>
                      {KIND[k].many}
                    </th>
                  ))}
                </tr>
                <tr>
                  {columns.map(([k, items]) =>
                    items.map((it, i) => (
                      <th key={it.id} scope="col" className={cn("bg-card sticky top-8 z-20 w-10 min-w-10 border-b px-0 pt-2 pb-1 align-bottom font-normal", i === 0 && "border-l")}>
                        <HrefLink href={href(k, it.id)} title={model.itemName(k, it.id)} className="text-foreground mx-auto block max-h-40 truncate text-sm [writing-mode:vertical-rl] rotate-180">
                          {model.itemName(k, it.id)}
                        </HrefLink>
                        <span className="text-muted-foreground mt-1 block text-center text-xs tabular-nums" title={`${usedBy(it.id)} of ${cfg.agents.length} agents get it`}>
                          {usedBy(it.id)}
                        </span>
                      </th>
                    )),
                  )}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const one = r.agents.length === 1;
                  const expanded = one || open.has(r.key);
                  return (
                    <Fragment key={r.key}>
                      {!one && (
                        <tr className="bg-[var(--band)]">
                          <th scope="row" className="sticky left-0 z-10 border-r border-b bg-[var(--band)] p-0 text-left font-medium">
                            <button
                              type="button"
                              aria-expanded={expanded}
                              onClick={() => setOpen((s) => (s.has(r.key) ? new Set([...s].filter((x) => x !== r.key)) : new Set(s).add(r.key)))}
                              className="hover:bg-muted flex w-full cursor-pointer items-center gap-1.5 px-3 py-2 text-sm whitespace-nowrap"
                            >
                              <ChevronRight className={cn("text-muted-foreground size-4 shrink-0 transition-transform", expanded && "rotate-90")} />
                              <span className="font-semibold tabular-nums">{r.agents.length} agents</span>
                              <span className="text-muted-foreground max-w-48 truncate">{r.agents.slice(0, 3).join(", ")}{r.agents.length > 3 ? ", …" : ""}</span>
                            </button>
                          </th>
                          {blocks.map((b, i) => (
                            <td key={b.id} className={cn("border-b p-0 text-center", (i === 0 || blocks[i - 1].kind !== b.kind) && "border-l")}>
                              {foldedCell(r.agents, b.id)}
                            </td>
                          ))}
                        </tr>
                      )}
                      {expanded &&
                        r.agents.map((a) => (
                          <tr key={a}>
                            <th scope="row" className="bg-card sticky left-0 z-10 border-r border-b px-3 py-1.5 text-left text-sm font-medium whitespace-nowrap">
                              <HrefLink href={href("agent", a)} className={cn(!one && "pl-6")}>
                                {a}
                              </HrefLink>
                            </th>
                            {blocks.map((b, i) => (
                              <td key={b.id} className={cn("border-b p-0 text-center", (i === 0 || blocks[i - 1].kind !== b.kind) && "border-l")}>
                                {cell(a, b.id)}
                              </td>
                            ))}
                          </tr>
                        ))}
                    </Fragment>
                  );
                })}
                {!rows.length && (
                  <tr>
                    <td colSpan={blocks.length + 1} className="text-muted-foreground px-3 py-6 text-sm">
                      No agent matches “{query}”.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <p className="text-muted-foreground mt-2 text-xs">
            <span className="bg-brand-soft text-primary rounded px-1 font-bold">✓</span> listed by the agent · <span className="font-medium">✓</span> through a group · the number under a block is how many agents get it
          </p>
        </>
      ) : (
        <Note>No agents yet.</Note>
      )}
    </Page>
  );
}
