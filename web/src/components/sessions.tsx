// Sessions: the live list with filters, one session's transcript, and the workspace's ingest token (Settings).
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Radio } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { type SessionDetail, type SessionFilters, type SessionSummary, type SessionTurn, call, q, sdk } from "@/lib/api";
import { when } from "@/lib/kinds";
import { useSessionStream } from "@/lib/live";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ConfirmButton, ErrorBanner, Note, PageTitle, Panel, Sub, Tag } from "./bits";
import { HrefLink, useGo } from "./href-link";
import { Page } from "./item-shell";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";

type Status = SessionSummary["status"];

const STATUS: Record<Status, { label: string; tone: string; help: string }> = {
  received: { label: "received", tone: "", help: "Stored and normalized. SOP checks come next." },
  routed: { label: "routed", tone: "bg-brand-soft text-primary border-transparent", help: "Matched to the procedures that applied." },
  checked: { label: "checked", tone: "bg-brand-soft text-primary border-transparent", help: "Checked against its procedures." },
  excluded: { label: "excluded", tone: "", help: "Ended before a procedure started or finished." },
  partial: { label: "partial", tone: "bg-warn-soft text-warn border-transparent", help: "The transcript may be incomplete." },
  failed: { label: "failed", tone: "bg-err-soft text-err border-transparent", help: "Processing failed." },
};

export function StatusTag({ status, big }: { status: Status; big?: boolean }) {
  const s = STATUS[status];
  return (
    <span title={s.help}>
      <Tag big={big} className={s.tone}>
        {s.label}
      </Tag>
    </span>
  );
}

/** 83.4 → "1:23" */
export function clock(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60));
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${mm.padStart(2, "0")}:${ss}` : `${mm}:${ss}`;
}

const agentName = (s: SessionSummary) => s.agent_id ?? s.agent_ref ?? "Unknown agent";

// --- list ---------------------------------------------------------------------

const ANY = "any";

function FilterSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T | undefined;
  options: [T, string][];
  onChange: (v: T | undefined) => void;
}) {
  return (
    <Select value={value ?? ANY} onValueChange={(v) => onChange(v === ANY ? undefined : (v as T))}>
      <SelectTrigger size="sm" className="min-w-36" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ANY}>{`Any ${label.toLowerCase()}`}</SelectItem>
        {options.map(([v, l]) => (
          <SelectItem key={v} value={v}>
            {l}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function SessionsView({ filters, onFilters }: { filters: SessionFilters; onFilters: (f: SessionFilters) => void }) {
  const { ws, cfg } = useWs();
  const go = useGo();
  const { connected, fresh } = useSessionStream(ws);
  const list = useInfiniteQuery(q.sessions(ws, filters));
  const rows = list.data?.pages.flatMap((p) => p.items) ?? [];
  const set = (patch: Partial<SessionFilters>) => onFilters({ ...filters, ...patch });
  const filtered = Object.values(filters).some(Boolean);
  const agents = [...new Set([...cfg.agents.map((a) => a.id), ...(filters.agent ? [filters.agent] : [])])];

  return (
    <Page wide>
      <PageTitle
        aside={
          <span className={cn("ml-1 flex items-center gap-1.5 text-[13px]", connected ? "text-ok" : "text-muted-foreground")} title={connected ? "New sessions appear as they arrive." : "Reconnecting…"}>
            <Radio className="size-3.5" /> {connected ? "Live" : "Offline"}
          </span>
        }
      >
        Sessions
      </PageTitle>
      <Sub>
        Every voice call and text chat your agents had, newest first. Send them from the <HrefLink href="https://github.com/amanmibra/sopops#sessions">LiveKit helper</HrefLink>, the API or{" "}
        <code className="text-[13px]">sopops import</code>, with the ingest token from <HrefLink href={wsHref(ws, "settings")}>Settings</HrefLink>.
      </Sub>

      <div className="mb-4 flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
        <FilterSelect label="Agent" value={filters.agent} options={agents.map((a) => [a, a])} onChange={(agent) => set({ agent })} />
        <FilterSelect label="Channel" value={filters.channel} options={[["voice", "Voice"], ["text", "Text"]]} onChange={(channel) => set({ channel })} />
        <FilterSelect label="Status" value={filters.status} options={(Object.keys(STATUS) as Status[]).map((s) => [s, s])} onChange={(status) => set({ status })} />
        <label className="text-muted-foreground flex items-center gap-1.5 text-[13px]">
          From
          <Input type="datetime-local" className="h-8 w-auto" value={filters.since ?? ""} onChange={(e) => set({ since: e.target.value || undefined })} />
        </label>
        <label className="text-muted-foreground flex items-center gap-1.5 text-[13px]">
          to
          <Input type="datetime-local" className="h-8 w-auto" value={filters.until ?? ""} onChange={(e) => set({ until: e.target.value || undefined })} />
        </label>
        {filtered && (
          <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={() => onFilters({})}>
            Clear
          </Button>
        )}
      </div>

      {list.isError && <ErrorBanner error={list.error} />}
      {list.isPending ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : rows.length === 0 ? (
        <Note>{filtered ? "No sessions match these filters." : "No sessions yet. They show up here as soon as they're sent."}</Note>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                {["Started", "Agent", "Channel", "Duration", "Turns", "Release", "Status"].map((h) => (
                  <th key={h} className="text-muted-foreground border-b px-2.5 py-2 text-left text-[13px] font-semibold whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr
                  key={s.id}
                  className={cn("cursor-pointer transition-colors duration-1000 hover:bg-muted", fresh.has(s.id) && "bg-brand-soft")}
                  onClick={() => go(wsHref(ws, "sessions", s.id))}
                >
                  <td className="border-b px-2.5 py-2 whitespace-nowrap">
                    <HrefLink href={wsHref(ws, "sessions", s.id)} className="text-foreground" onClick={(e) => e.stopPropagation()}>
                      {when(s.started_at)}
                    </HrefLink>
                  </td>
                  <td className="border-b px-2.5 py-2">
                    <span className={cn(!s.agent_id && "text-muted-foreground")} title={s.agent_id ? undefined : "Not matched to an agent in sopops"}>
                      {agentName(s)}
                    </span>
                    <div className="text-muted-foreground text-xs">{s.platform}</div>
                  </td>
                  <td className="border-b px-2.5 py-2 capitalize">{s.channel}</td>
                  <td className="border-b px-2.5 py-2 font-mono text-[13px]">{clock(s.duration_s) || "—"}</td>
                  <td className="border-b px-2.5 py-2 font-mono text-[13px]">{s.turn_count}</td>
                  <td className="border-b px-2.5 py-2 text-[13px]">{s.release_id != null ? `#${s.release_id}` : <span className="text-muted-foreground">—</span>}</td>
                  <td className="border-b px-2.5 py-2">
                    <StatusTag status={s.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {list.hasNextPage && (
        <div className="mt-4">
          <Button variant="outline" size="sm" disabled={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>
            {list.isFetchingNextPage ? "Loading…" : "Older sessions"}
          </Button>
        </div>
      )}
    </Page>
  );
}

// --- one session ----------------------------------------------------------------

const ROLE: Record<SessionTurn["role"], { label: string; className: string }> = {
  agent: { label: "Agent", className: "text-primary font-semibold" },
  user: { label: "User", className: "text-foreground font-semibold" },
  tool: { label: "Tool", className: "text-muted-foreground" },
  system: { label: "System", className: "text-muted-foreground" },
};

function Turn({ turn }: { turn: SessionTurn }) {
  const r = ROLE[turn.role];
  const extra = turn as SessionTurn & { name?: string; interrupted?: boolean };
  const quiet = turn.role === "tool" || turn.role === "system";
  return (
    <li className={cn("grid grid-cols-[52px_64px_minmax(0,1fr)] gap-3 border-b py-2 last:border-b-0", quiet && "text-[13px]")}>
      <span className="text-muted-foreground pt-px font-mono text-xs">{clock(turn.offset_s)}</span>
      <span className={cn("text-[13px]", r.className)}>{r.label}</span>
      <span className={cn("whitespace-pre-wrap", turn.role === "tool" && "font-mono text-muted-foreground", turn.role === "system" && "italic text-muted-foreground")}>
        {turn.text || (extra.name ? extra.name : <span className="text-muted-foreground">(no text)</span>)}
        {extra.interrupted && <Tag className="ml-2 align-middle">interrupted</Tag>}
      </span>
    </li>
  );
}

const json = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v, null, 2));

export function SessionView({ id }: { id: string }) {
  const { ws, releases } = useWs();
  useSessionStream(ws);
  const res = useQuery(q.session(ws, id));
  if (res.isPending) return <Page><p className="text-muted-foreground">Loading…</p></Page>;
  if (res.isError) return <Page><ErrorBanner error={res.error} /><HrefLink href={wsHref(ws, "sessions")}>All sessions</HrefLink></Page>;
  const s: SessionDetail = res.data;
  const release = s.release_id != null ? releases.find((r) => r.number === s.release_id) : undefined;
  const facts: [string, React.ReactNode][] = [
    ["Agent", s.agent_id ? <HrefLink href={wsHref(ws, "agents", s.agent_id)}>{s.agent_id}</HrefLink> : (s.agent_ref ?? "—")],
    ["Release", s.release_id != null ? <HrefLink href={wsHref(ws, "releases", s.release_id)}>#{s.release_id}{release?.note ? `: ${release.note}` : ""}</HrefLink> : "—"],
    ["Platform version", s.version_ref ?? "—"],
    ["Platform", `${s.platform} (${s.channel})`],
    ["Platform id", <code className="text-[13px] break-all">{s.external_id}</code>],
    ["Started", when(s.started_at)],
    ["Ended", s.ended_at ? `${when(s.ended_at)} (${clock(s.duration_s)})` : "—"],
    ["Processed", s.processed_at ? when(s.processed_at) : "Not yet"],
  ];
  if (s.agent_ref && s.agent_id && s.agent_ref !== s.agent_id) facts.splice(1, 0, ["Sent as", s.agent_ref]);
  return (
    <Page wide>
      <div className="text-muted-foreground mb-2 text-[13px]">
        <HrefLink href={wsHref(ws, "sessions")}>Sessions</HrefLink> /
      </div>
      <PageTitle aside={<StatusTag status={s.status} big />}>
        {agentName(s)}, {when(s.started_at)}
      </PageTitle>
      <Sub>
        {s.turn_count} turns{s.tool_call_count ? `, ${s.tool_call_count} tool calls` : ""}
        {s.duration_s != null ? `, ${clock(s.duration_s)} long` : ""}.
      </Sub>
      {s.status_reason && <Banner tone={s.status === "failed" ? "err" : "warn"}>{s.status_reason}</Banner>}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel title="Transcript" className="min-w-0">
          {s.turns.length ? (
            <ol>
              {s.turns.map((t, i) => (
                <Turn key={i} turn={t} />
              ))}
            </ol>
          ) : (
            <Note>No turns.</Note>
          )}
        </Panel>
        <div className="min-w-0">
          <Panel title="Details">
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
              {facts.map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
          </Panel>
          {s.tool_calls.length > 0 && (
            <Panel title="Tool calls">
              <ul className="grid gap-2.5">
                {s.tool_calls.map((t, i) => (
                  <li key={i} className="text-[13px]">
                    <div className="font-mono font-semibold">
                      {String(t.name ?? "tool")}
                      {t.is_error ? <Tag className="bg-err-soft text-err ml-2 border-transparent">error</Tag> : null}
                    </div>
                    {t.arguments != null && <pre className="bg-muted mt-1 overflow-x-auto rounded-md px-2 py-1 text-xs">{json(t.arguments)}</pre>}
                    {t.result != null && <pre className="bg-muted mt-1 overflow-x-auto rounded-md px-2 py-1 text-xs">→ {json(t.result)}</pre>}
                  </li>
                ))}
              </ul>
            </Panel>
          )}
          <Panel title="Metadata">
            {Object.keys(s.metadata).length ? (
              <pre className="overflow-x-auto text-xs">{json(s.metadata)}</pre>
            ) : (
              <Note>None.</Note>
            )}
          </Panel>
        </div>
      </div>
    </Page>
  );
}

// --- ingest token (Settings) -------------------------------------------------------

export function IngestTokenPanel() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const info = useQuery(q.ingestToken(ws));
  const [shown, setShown] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const url = `${window.location.origin}/v1/workspaces/${encodeURIComponent(ws)}`;

  const rotate = async () => {
    setBusy(true);
    try {
      const made = await call(sdk.rotateIngestToken({ path: { workspace: ws } }));
      setShown(made.token);
      await qc.invalidateQueries({ queryKey: ["ws", ws, "ingest-token"] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const revoke = async () => {
    try {
      await call(sdk.revokeIngestToken({ path: { workspace: ws } }));
      setShown(null);
      await qc.invalidateQueries({ queryKey: ["ws", ws, "ingest-token"] });
      toast.success("Ingest token deleted. Sessions are refused until you make a new one.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };
  const copy = (text: string) => navigator.clipboard.writeText(text).then(() => toast.success("Copied."));

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <KeyRound className="size-4" /> Ingest token
        </span>
      }
      className="mt-8"
    >
      <p className="text-muted-foreground mb-3 text-[13px]">
        Agents and webhooks send sessions to <code>{url}/sessions</code> with <code>Authorization: Bearer &lt;token&gt;</code>. It's only for sending
        sessions; the rest of sopops has no sign-in yet.
      </p>
      {info.isError && <ErrorBanner error={info.error} />}
      {shown && (
        <Banner tone="ok">
          <b>Copy this token now. It isn't shown again.</b>
          <div className="bg-card mt-2 flex items-center gap-2 rounded-md border px-2.5 py-1.5">
            <code className="flex-1 font-mono text-[13px] break-all">{shown}</code>
            <Button size="icon-sm" variant="ghost" aria-label="Copy token" onClick={() => copy(shown)}>
              <Copy />
            </Button>
          </div>
          <pre className="mt-2 overflow-x-auto text-xs">{`export SOPOPS_URL=${url}\nexport SOPOPS_INGEST_TOKEN=${shown}`}</pre>
        </Banner>
      )}
      {info.data &&
        (info.data.exists ? (
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="text-[13px]">
              <code>{info.data.prefix}…</code> <span className="text-muted-foreground">made {when(info.data.created_at)}</span>
            </span>
            <span className="flex-1" />
            <ConfirmButton size="sm" variant="outline" disabled={busy} confirm="Click again: the old token stops working" onConfirm={rotate}>
              Rotate
            </ConfirmButton>
            <ConfirmButton size="sm" variant="ghost" className="text-err" confirm="Click again to delete" onConfirm={revoke}>
              Delete
            </ConfirmButton>
          </div>
        ) : (
          <Button size="sm" disabled={busy} onClick={rotate}>
            Create ingest token
          </Button>
        ))}
    </Panel>
  );
}
