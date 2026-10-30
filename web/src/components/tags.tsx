// Tags on agents and sessions: chips, and an editor that saves as you change it (tags need no release).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useState } from "react";
import { call, q, sdk } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Input } from "./ui/input";

/** Lowercase, trimmed, spaces as dashes, as the server stores a tag. */
const normalize = (t: string) => t.trim().toLowerCase().replace(/\s+/g, "-");
const VALID = /^[a-z0-9][a-z0-9_.:/-]{0,63}$/;

export function TagChip({ tag, onRemove, className }: { tag: string; onRemove?: () => void; className?: string }) {
  return (
    <span className={cn("bg-muted text-muted-foreground inline-flex items-center gap-0.5 rounded-sm border px-1 text-xs whitespace-nowrap", className)}>
      #{tag}
      {onRemove && (
        <button type="button" className="hover:text-foreground -mr-0.5" aria-label={`Remove tag ${tag}`} onClick={onRemove}>
          <X className="size-3" />
        </button>
      )}
    </span>
  );
}

export function TagChips({ tags, className }: { tags: string[] | undefined; className?: string }) {
  if (!tags?.length) return null;
  return (
    <span className={cn("flex flex-wrap gap-1", className)}>
      {tags.map((t) => (
        <TagChip key={t} tag={t} />
      ))}
    </span>
  );
}

/** Every tag an agent or session in the workspace has, for filters and suggestions. */
export function useTags(ws: string) {
  const res = useQuery(q.agentTags(ws));
  return { used: res.data?.used ?? [], agents: res.data?.agents ?? {} };
}

/** Chips with a remove button, and a field that adds a tag on Enter or comma. `save` gets the whole new list. */
export function TagEditor({ tags, save, suggestions = [], label }: { tags: string[]; save: (tags: string[]) => Promise<unknown>; suggestions?: string[]; label: string }) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const commit = async (next: string[]) => {
    setBusy(true);
    setError(null);
    try {
      await save(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const add = (raw: string) => {
    const tag = normalize(raw);
    if (!tag) return;
    if (!VALID.test(tag)) return setError("Use letters, digits and - _ . : /, up to 64 characters.");
    setText("");
    if (!tags.includes(tag)) void commit([...tags, tag]);
  };
  const listId = `tags-${label.replace(/\W/g, "")}`;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5">
        {tags.map((t) => (
          <TagChip key={t} tag={t} className="text-sm" onRemove={busy ? undefined : () => void commit(tags.filter((x) => x !== t))} />
        ))}
        <Input
          className="h-7 w-44"
          value={text}
          list={listId}
          placeholder={tags.length ? "Add a tag" : "e.g. legacy or sopops"}
          aria-label={`Add a tag to ${label}`}
          disabled={busy}
          onChange={(e) => {
            const v = e.target.value;
            if (v.endsWith(",")) add(v.slice(0, -1));
            else setText(v);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add(text);
            } else if (e.key === "Backspace" && !text && tags.length) void commit(tags.slice(0, -1));
          }}
          onBlur={() => text && add(text)}
        />
        <datalist id={listId}>
          {suggestions.filter((s) => !tags.includes(s)).map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
      </div>
      {error && <div className="text-err mt-1 text-sm">{error}</div>}
    </div>
  );
}

/** An agent's tags, saved as they change. Sessions copy them when they come in, and keep that copy. */
export function AgentTags({ ws, agent }: { ws: string; agent: string }) {
  const qc = useQueryClient();
  const { used, agents } = useTags(ws);
  return (
    <TagEditor
      label={agent}
      tags={agents[agent] ?? []}
      suggestions={used}
      save={async (tags) => {
        await call(sdk.setAgentTags({ path: { workspace: ws, agent }, body: { tags } }));
        await qc.invalidateQueries({ queryKey: q.agentTags(ws).queryKey });
      }}
    />
  );
}

/** A session's tags, editable: they start as a copy of its agent's. */
export function SessionTags({ ws, id, tags }: { ws: string; id: string; tags: string[] }) {
  const qc = useQueryClient();
  const { used } = useTags(ws);
  return (
    <TagEditor
      label="this session"
      tags={tags}
      suggestions={used}
      save={async (next) => {
        await call(sdk.setSessionTags({ path: { workspace: ws, id }, body: { tags: next } }));
        await Promise.all([qc.invalidateQueries({ queryKey: q.session(ws, id).queryKey }), qc.invalidateQueries({ queryKey: ["ws", ws, "sessions"] }), qc.invalidateQueries({ queryKey: q.agentTags(ws).queryKey })]);
      }}
    />
  );
}
