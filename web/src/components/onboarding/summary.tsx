// The import review as summaries, the same at 5 agents or 100: one line of counts, then disagreements, variables,
// blocks by agent, each agent's unique text, and fidelity. Decisions are kept by the group's id.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import type { AgentFidelity, Disagreement, ReviewOut, UniqueText, VariableRow } from "@/client/types.gen";
import { call, sdk } from "@/lib/api";
import { plural } from "@/lib/kinds";
import { pct } from "@/lib/onboarding";
import { cn } from "@/lib/utils";
import { ErrorBanner, KindBadge, Note, Panel, Tag } from "../bits";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { UsageGrid } from "../usage-grid";

const PAGE = 10;
const AGENT_PAGE = 20;

export const reviewQuery = (ws: string, id: string) => ({
  queryKey: ["ws", ws, "import", id, "review"],
  queryFn: () => call(sdk.getImportReview({ path: { workspace: ws, id } })),
});

export function ReviewSummary({ ws, id, disabled }: { ws: string; id: string; disabled: boolean }) {
  const qc = useQueryClient();
  const q = useQuery(reviewQuery(ws, id));
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  if (q.error) return <ErrorBanner error={q.error} lead="The review couldn't be loaded:" />;
  const r = q.data;
  if (!r) return <Note>Loading the review…</Note>;

  const update = async (label: string, fn: () => Promise<ReviewOut>) => {
    setBusy(label);
    setError(null);
    try {
      qc.setQueryData(reviewQuery(ws, id).queryKey, await fn());
      await qc.invalidateQueries({ queryKey: ["ws", ws, "import", id], exact: true });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  const decide = (group: string, choice: string) => update(group, () => call(sdk.decideImportGroup({ path: { workspace: ws, id, group }, body: { choice } })));
  const editVariable = (name: string, body: { name?: string; values?: Record<string, string> }) =>
    update(`var:${name}`, () => call(sdk.editImportVariable({ path: { workspace: ws, id, name }, body: { name: body.name ?? null, values: body.values ?? {} } })));
  const off = disabled || !!busy;
  const c = r.counts;

  return (
    <>
      <Panel title="What was found">
        <p className="text-[15px]">
          {plural(c.agents, "agent", "agents")} → {plural(c.shared_blocks, "shared block", "shared blocks")}, {plural(c.variables, "variable", "variables")},{" "}
          <b className={cn(c.undecided && "text-warn")}>{plural(c.disagreements, "disagreement", "disagreements")}</b>
          {c.undecided ? ` (${c.undecided} to decide)` : ""}, {plural(c.agents_with_unique, "agent", "agents")} with unique content,{" "}
          <b className={cn(c.below_full_fidelity ? "text-err" : "text-ok")}>{c.below_full_fidelity} below 100% fidelity</b>
        </p>
        {disabled && <Note className="mt-2">Save your edits before deciding.</Note>}
      </Panel>
      {error ? <ErrorBanner error={error} lead="That didn't work:" /> : null}
      <Disagreements items={r.disagreements} disabled={off} onDecide={decide} />
      <Variables rows={r.variables} disabled={off} onEdit={editVariable} />
      <Blocks r={r} />
      <Unique items={r.unique} disabled={off} onDecide={decide} />
      <Fidelity rows={r.fidelity} />
    </>
  );
}

function Pager({ page, pages, onPage, what }: { page: number; pages: number; onPage: (p: number) => void; what: string }) {
  if (pages <= 1) return null;
  return (
    <div className="mt-3 flex items-center gap-2 text-[13px]">
      <Button size="xs" variant="outline" disabled={page === 0} onClick={() => onPage(page - 1)}>
        Previous
      </Button>
      <span className="text-muted-foreground">
        {what} {page + 1} of {pages}
      </span>
      <Button size="xs" variant="outline" disabled={page >= pages - 1} onClick={() => onPage(page + 1)}>
        Next
      </Button>
    </div>
  );
}

function usePage<T>(items: T[], size: number) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(items.length / size));
  const p = Math.min(page, pages - 1);
  return { page: p, pages, setPage, slice: items.slice(p * size, (p + 1) * size) };
}

const agentList = (agents: string[], max = 6) => (agents.length <= max ? agents.join(", ") : `${agents.slice(0, max).join(", ")} and ${agents.length - max} more`);

function Disagreements({ items, disabled, onDecide }: { items: Disagreement[]; disabled: boolean; onDecide: (group: string, choice: string) => void }) {
  const { page, pages, setPage, slice } = usePage(items, PAGE);
  if (!items.length) return null;
  return (
    <Panel title={`Disagreements (${items.length})`}>
      <p className="text-muted-foreground mb-3 text-[13px]">A rule word differs between agents (a count, a never, a must). Until you decide, each agent keeps what it says today. Smallest minorities first.</p>
      <div className="grid gap-3">
        {slice.map((d) => (
          <div key={d.id} className={cn("rounded-lg border px-3.5 py-3", d.decision ? "bg-muted" : "bg-warn-soft border-transparent")}>
            <div className="mb-2 flex flex-wrap items-center gap-2 text-[13px]">
              <b>
                {d.minority} of {d.minority + d.majority.agents.length} agents differ
              </b>
              {d.source === "model" && <Tag>suggested by the model</Tag>}
              {d.decision && <Tag tone="ok">{d.decision === "keep" ? "each keeps its own" : `everyone: ${d.decision.slice(4)}'s wording`}</Tag>}
            </div>
            <Wording label={`Most agents (${d.majority.agents.length})`} text={d.majority.text} agents={d.majority.agents}>
              <Button size="sm" variant="outline" disabled={disabled} onClick={() => onDecide(d.id, `all:${d.majority.agents[0]}`)}>
                Use for all
              </Button>
            </Wording>
            {d.outliers.map((o, i) => (
              <Wording key={i} label={agentList(o.agents)} text={o.text} agents={o.agents}>
                <Button size="sm" variant="outline" disabled={disabled} onClick={() => onDecide(d.id, `all:${o.agents[0]}`)}>
                  Use for all
                </Button>
              </Wording>
            ))}
            <Button size="sm" variant="ghost" className="mt-2" disabled={disabled} onClick={() => onDecide(d.id, "keep")}>
              Keep both
            </Button>
          </div>
        ))}
      </div>
      <Pager page={page} pages={pages} onPage={setPage} what="Page" />
    </Panel>
  );
}

function Wording({ label, text, children }: { label: string; text: string; agents: string[]; children: ReactNode }) {
  return (
    <div className="bg-card mb-2 flex flex-wrap items-start gap-3 rounded-md border px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="text-muted-foreground mb-0.5 text-[12px]">{label}</div>
        <div className="text-[13px] whitespace-pre-wrap">{text}</div>
      </div>
      {children}
    </div>
  );
}

function Variables({ rows, disabled, onEdit }: { rows: VariableRow[]; disabled: boolean; onEdit: (name: string, body: { name?: string; values?: Record<string, string> }) => void }) {
  const agents = Object.keys(rows[0]?.values ?? {});
  const { page, pages, setPage, slice } = usePage(agents, AGENT_PAGE);
  if (!rows.length) return null;
  return (
    <Panel title={`Variables (${rows.length})`}>
      <p className="text-muted-foreground mb-3 text-[13px]">Values that differ between agents. Edit a value or rename a variable; names marked “found by sopc” are defaults.</p>
      <div className="max-h-[60vh] overflow-auto rounded-md border">
        <table className="min-w-full border-collapse text-[13px]">
          <thead>
            <tr>
              <th className="bg-card sticky top-0 left-0 z-20 min-w-[200px] border-b px-2 py-2 text-left">Variable</th>
              {slice.map((a) => (
                <th key={a} className="bg-card sticky top-0 z-10 border-b px-2 py-2 text-left whitespace-nowrap">
                  {a}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((v) => (
              <tr key={v.name}>
                <th className="bg-card sticky left-0 border-b px-2 py-1 text-left font-medium">
                  <EditText value={v.name} disabled={disabled || v.source !== "proposal"} onSave={(name) => onEdit(v.name, { name })} mono />
                  {v.source === "overlap" && <Tag>found by sopc</Tag>}
                </th>
                {slice.map((a) => (
                  <td key={a} className="min-w-[140px] border-b border-l px-1 py-1">
                    <EditText value={v.values[a] ?? ""} disabled={disabled || v.source !== "proposal"} onSave={(value) => onEdit(v.name, { values: { [a]: value } })} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} pages={pages} onPage={setPage} what="Agents page" />
    </Panel>
  );
}

function EditText({ value, disabled, onSave, mono }: { value: string; disabled: boolean; onSave: (v: string) => void; mono?: boolean }) {
  const [text, setText] = useState(value);
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    setText(value);
  }
  return (
    <Input
      className={cn("h-7 text-[13px]", mono && "font-mono")}
      value={text}
      disabled={disabled}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => text !== value && onSave(text)}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    />
  );
}

function Blocks({ r }: { r: ReviewOut }) {
  const agents = r.fidelity.map((f) => f.agent);
  const all = agents.length ? agents : [...new Set(r.blocks.flatMap((b) => b.agents))];
  const { page, pages, setPage, slice } = usePage(all, AGENT_PAGE);
  if (!r.blocks.length) return null;
  const sections = (["instruction", "procedure"] as const).map((kind) => ({
    key: kind,
    title: kind === "instruction" ? "Shared instructions" : "Procedures",
    rows: r.blocks
      .filter((b) => b.kind === kind)
      .map((b) => ({
        id: b.id,
        head: (
          <>
            <KindBadge kind={kind} /> {b.title}
          </>
        ),
        cell: (agent: string) =>
          b.agents.includes(agent) ? (
            <span className="text-primary font-bold">✓</span>
          ) : b.missing.includes(agent) ? (
            <span className="text-warn text-[12px] font-semibold" title="Most agents have this text; this one doesn't.">
              missing
            </span>
          ) : null,
      })),
  }));
  return (
    <Panel title="Blocks by agent">
      <p className="text-muted-foreground mb-3 text-[13px]">Who uses each proposed block, and who lacks text most agents have.</p>
      <UsageGrid columns={slice.map((a) => ({ id: a, head: a }))} sections={sections} cellClass="min-w-[90px]" />
      <Pager page={page} pages={pages} onPage={setPage} what="Agents page" />
    </Panel>
  );
}

function Unique({ items, disabled, onDecide }: { items: UniqueText[]; disabled: boolean; onDecide: (group: string, choice: string) => void }) {
  const { page, pages, setPage, slice } = usePage(items, PAGE);
  if (!items.length) return null;
  return (
    <Panel title={`Unique to one agent (${items.length})`}>
      <p className="text-muted-foreground mb-3 text-[13px]">Text no other agent has. It stays in the agent's own context unless you make it a block.</p>
      <div className="grid gap-2">
        {slice.map((u) => (
          <div key={u.id} className="bg-card flex flex-wrap items-start gap-3 rounded-md border px-3 py-2">
            <div className="min-w-0 flex-1">
              <div className="text-muted-foreground mb-0.5 text-[12px]">{u.agent}</div>
              <div className="text-[13px] whitespace-pre-wrap">{u.text}</div>
            </div>
            <Button size="sm" variant={u.decision === "context" ? "secondary" : "outline"} disabled={disabled || u.decision === "context"} onClick={() => onDecide(u.id, "context")}>
              Keep as this agent's context
            </Button>
            <Button size="sm" variant={u.decision === "block" ? "secondary" : "outline"} disabled={disabled || u.decision === "block"} onClick={() => onDecide(u.id, "block")}>
              Make it a block
            </Button>
          </div>
        ))}
      </div>
      <Pager page={page} pages={pages} onPage={setPage} what="Page" />
    </Panel>
  );
}

function Fidelity({ rows }: { rows: AgentFidelity[] }) {
  const [all, setAll] = useState(false);
  const failing = rows.filter((f) => !f.ok || f.score < 1);
  const shown = all ? rows : failing;
  const { page, pages, setPage, slice } = usePage(shown, AGENT_PAGE);
  if (!rows.length) return null;
  return (
    <Panel title="Fidelity">
      <div className="mb-3 flex items-center gap-3 text-[13px]">
        <span>{failing.length ? `${plural(failing.length, "agent loses", "agents lose")} or change text.` : "Every agent keeps all of its text."}</span>
        <Button size="xs" variant="outline" onClick={() => setAll(!all)}>
          {all ? "Only failing agents" : `Show all ${rows.length}`}
        </Button>
      </div>
      {shown.length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <table className="min-w-full border-collapse text-[13px]">
            <thead>
              <tr className="text-left">
                <th className="border-b px-3 py-2">Agent</th>
                <th className="border-b px-3 py-2">Kept</th>
                <th className="border-b px-3 py-2">Lost</th>
                <th className="border-b px-3 py-2">Changed</th>
                <th className="border-b px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {slice.map((f) => (
                <FidelityRow key={f.agent} f={f} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pager page={page} pages={pages} onPage={setPage} what="Page" />
    </Panel>
  );
}

function FidelityRow({ f }: { f: AgentFidelity }) {
  const [open, setOpen] = useState(false);
  const missing = f.missing ?? [];
  const changed = f.changed ?? [];
  return (
    <>
      <tr>
        <td className="border-b px-3 py-1.5 font-medium">{f.agent}</td>
        <td className={cn("border-b px-3 py-1.5", f.ok ? "text-ok" : "text-err")}>{pct(f.score)}</td>
        <td className="border-b px-3 py-1.5">{missing.length}</td>
        <td className="border-b px-3 py-1.5">{changed.length}</td>
        <td className="border-b px-3 py-1.5 text-right">
          {missing.length + changed.length > 0 && (
            <Button size="xs" variant="ghost" onClick={() => setOpen(!open)}>
              {open ? "Hide" : "Show"}
            </Button>
          )}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={5} className="bg-muted border-b px-3 py-2">
            <ul className="grid gap-1">
              {missing.map((m, i) => (
                <li key={`m${i}`}>
                  <Tag tone="err">lost</Tag> {m}
                </li>
              ))}
              {changed.map((c, i) => (
                <li key={`c${i}`}>
                  <Tag tone="warn">changed</Tag> <s className="text-muted-foreground">{c.original}</s> → {c.now}
                </li>
              ))}
            </ul>
          </td>
        </tr>
      )}
    </>
  );
}
