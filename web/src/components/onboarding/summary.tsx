// The parts of the import review that read the same at 5 agents or 100: disagreements, variables and each agent's
// unique text, paged. Decisions are kept by the group's id.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import type { Disagreement, ReviewOut, UniqueText, VariableRow } from "@/client/types.gen";
import { call, sdk } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Tag } from "../bits";
import { Hint, TipBody } from "../insights/kit";
import { Button } from "../ui/button";
import { Input } from "../ui/input";

const PAGE = 10;
const AGENT_PAGE = 20;

export const reviewQuery = (ws: string, id: string) => ({
  queryKey: ["ws", ws, "import", id, "review"],
  queryFn: () => call(sdk.getImportReview({ path: { workspace: ws, id } })),
});

/** The review and the calls that change it: deciding a disagreement or a unique text, and editing a variable. */
export function useReview(ws: string, id: string) {
  const qc = useQueryClient();
  const q = useQuery(reviewQuery(ws, id));
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
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
  return { q, r: q.data, busy, error, decide, editVariable };
}

function Pager({ page, pages, onPage, what }: { page: number; pages: number; onPage: (p: number) => void; what: string }) {
  if (pages <= 1) return null;
  return (
    <div className="mt-3 flex items-center gap-2 text-sm">
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

export function Disagreements({ items, disabled, onDecide }: { items: Disagreement[]; disabled: boolean; onDecide: (group: string, choice: string) => void }) {
  const { page, pages, setPage, slice } = usePage(items, PAGE);
  if (!items.length) return null;
  return (
    <div>
      <div className="grid gap-3">
        {slice.map((d) => (
          <div key={d.id} className={cn("rounded-lg border px-4 py-3", d.decision ? "bg-muted" : "bg-warn-soft border-transparent")}>
            <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
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
    </div>
  );
}

function Wording({ label, text, children }: { label: string; text: string; agents: string[]; children: ReactNode }) {
  return (
    <div className="bg-card mb-2 flex flex-wrap items-start gap-3 rounded-md border px-3 py-2">
      <div className="min-w-[min(16rem,100%)] flex-1">
        <div className="text-muted-foreground mb-0.5 text-sm">{label}</div>
        <div className="text-sm whitespace-pre-wrap">{text}</div>
      </div>
      {children}
    </div>
  );
}

export function Variables({ rows, disabled, onEdit }: { rows: VariableRow[]; disabled: boolean; onEdit: (name: string, body: { name?: string; values?: Record<string, string> }) => void }) {
  const agents = Object.keys(rows[0]?.values ?? {});
  const { page, pages, setPage, slice } = usePage(agents, AGENT_PAGE);
  if (!rows.length) return null;
  return (
    <div>
      <div className="max-h-[60vh] overflow-auto rounded-md border">
        <table className="min-w-full border-collapse text-sm">
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
                  {v.source === "overlap" && (
                    <Hint tip={<TipBody>sopops saw this text is the same in every agent except for this value, so it made it a variable. Its name is a placeholder.</TipBody>} className="mt-1 inline-block cursor-help">
                      <Tag>Spotted automatically</Tag>
                    </Hint>
                  )}
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
    </div>
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
      className={cn("h-7 text-sm", mono && "font-mono")}
      value={text}
      disabled={disabled}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => text !== value && onSave(text)}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    />
  );
}

export function Unique({ items, disabled, onDecide }: { items: UniqueText[]; disabled: boolean; onDecide: (group: string, choice: string) => void }) {
  const { page, pages, setPage, slice } = usePage(items, PAGE);
  if (!items.length) return null;
  return (
    <div>
      <div className="grid gap-2">
        {slice.map((u) => (
          <div key={u.id} className="bg-card flex flex-wrap items-start gap-3 rounded-md border px-3 py-2">
            <div className="min-w-[min(16rem,100%)] flex-1">
              <div className="text-muted-foreground mb-0.5 text-sm">{u.agent}</div>
              <div className="text-sm whitespace-pre-wrap">{u.text}</div>
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
    </div>
  );
}
