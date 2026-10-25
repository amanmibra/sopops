// Sessions: the live list with filters, and one session's transcript.
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Maximize2, Radio, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { type SessionDetail, type SessionFilters, type SessionSummary, type SessionTurn, q } from "@/lib/api";
import { STATUS_MARK, ruleName } from "@/lib/insights";
import { plural, when } from "@/lib/kinds";
import { useSessionStream } from "@/lib/live";
import { cn } from "@/lib/utils";
import { settingsHref, useWs, wsHref } from "@/lib/workspace";
import { ChecksPanel, Highlighted, RESULT, type Result, ResultTag, type TurnMarks, turnMarks } from "./checks";
import { Banner, ErrorBanner, Note, PageTitle, Panel, Segmented, Sub, Tag } from "./bits";
import { JsonBlock, JsonDialog, JsonInline } from "./json-view";
import { usePlatforms } from "@/lib/platforms";
import { PlatformLabel } from "./brand-icon";
import { HrefLink, useGo } from "./href-link";
import { Page } from "./item-shell";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle } from "./ui/sheet";
import { Hint } from "./insights/kit";
import { SessionCost } from "./insights/spend";

type Status = SessionSummary["status"];

const STATUS: Record<Status, { label: string; tone: string; help: string }> = {
  open: { label: "open", tone: "", help: "Streaming in; checked when it closes." },
  received: { label: "received", tone: "", help: "Stored and normalized, not checked yet." },
  routed: { label: "routed", tone: "bg-brand-soft text-primary border-transparent", help: "Matched to the procedures that applied." },
  checked: { label: "checked", tone: "bg-brand-soft text-primary border-transparent", help: "Checked against its procedures." },
  excluded: { label: "excluded", tone: "", help: "Ended before a procedure started or finished." },
  incomplete: { label: "incomplete", tone: "bg-warn-soft text-warn border-transparent", help: "Turns are missing: the sender said the transcript is incomplete, it had no turns, or it stopped streaming. Still checked on what came in." },
  failed: { label: "failed", tone: "bg-err-soft text-err border-transparent", help: "Processing failed." },
};

const NUMERIC = new Set(["Duration", "Turns", "Release"]);

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

export function FilterSelect<T extends string>({
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
      <SelectTrigger size="sm" className="min-w-28" aria-label={label}>
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

const DRILL: (keyof SessionFilters)[] = ["eligible", "release", "procedure", "procedure_result", "rule", "rule_status", "pattern", "theme", "finding"];

/** The drill-down filters Insights links set, as removable chips. */
function DrillChips({ filters, onFilters }: { filters: SessionFilters; onFilters: (f: SessionFilters) => void }) {
  const { ws, cfg } = useWs();
  const pattern = useQuery({ ...q.pattern(ws, filters.pattern ?? ""), enabled: !!filters.pattern });
  const rules = useQuery({ ...q.patterns(ws, filters.procedure), enabled: !!filters.rule || !!filters.theme });
  const active = DRILL.filter((k) => filters[k] != null && !(Array.isArray(filters[k]) && !(filters[k] as unknown[]).length));
  if (!active.length) return null;
  const procName = (id?: string) => cfg.procedures.find((p) => p.id === id)?.name ?? id;
  const rulePat = rules.data?.items.find((p) => p.rule_id === filters.rule);
  const label = (k: keyof SessionFilters): string => {
    switch (k) {
      case "release":
        return `Release #${filters.release}`;
      case "procedure":
        return `Procedure: ${procName(filters.procedure)}`;
      case "procedure_result":
        return `Procedure result: ${RESULT[filters.procedure_result!].label}`;
      case "rule":
        return `Rule: ${rulePat?.rule?.kind ? ruleName(rulePat.rule.kind, rulePat.rule.index) : filters.rule}`;
      case "rule_status":
        return `Rule status: ${filters.rule_status!.map((x) => x.replace("_", " ")).join(" or ")}`;
      case "pattern":
        return `Pattern: ${pattern.data?.name ?? "…"}`;
      case "theme": {
        const named = rules.data?.items.flatMap((p) => [p, ...p.sub_patterns]).find((p) => p.theme_id === filters.theme && p.theme_name);
        return `Theme: ${named?.theme_name ?? "…"}`;
      }
      case "eligible":
        return filters.eligible ? "Checked (rated by Insights)" : "Not rated by Insights";
      case "finding":
        return filters.finding ? "With findings" : "Without findings";
      default:
        return k;
    }
  };
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2" aria-label="Drill-down filters">
      {active.map((k) => (
        <span key={k} className="bg-brand-soft inline-flex items-center gap-1 rounded-full py-0.5 pr-1 pl-2 text-sm">
          {label(k)}
          <button
            type="button"
            aria-label={`Remove ${label(k)}`}
            className="hover:bg-card text-muted-foreground cursor-pointer rounded-full px-2"
            onClick={() => onFilters({ ...filters, [k]: undefined, ...(k === "rule" ? { rule_status: undefined } : {}) })}
          >
            ×
          </button>
        </span>
      ))}
    </div>
  );
}

export function SessionsView({ filters, onFilters }: { filters: SessionFilters; onFilters: (f: SessionFilters) => void }) {
  const { ws, cfg } = useWs();
  const plats = usePlatforms();
  const go = useGo();
  const { connected, fresh } = useSessionStream(ws);
  const list = useInfiniteQuery(q.sessions(ws, filters));
  const rows = list.data?.pages.flatMap((p) => p.items) ?? [];
  const set = (patch: Partial<SessionFilters>) => onFilters({ ...filters, ...patch });
  const filtered = Object.values(filters).some((v) => v != null && v !== "" && !(Array.isArray(v) && !v.length));
  const agents = [...new Set([...cfg.agents.map((a) => a.id), ...(filters.agent ? [filters.agent] : [])])];
  const [openId, setOpenId] = useState<string | null>(null);
  const focus = !!filters.procedure;
  const outcomeKey = focus ? "procedure_result" : "result";
  const outcome = (focus ? filters.procedure_result : filters.result) ?? "all";
  const ids = rows.map((r) => r.id);
  const showStatus = rows.some((r) => r.status !== "checked");

  return (
    <Page wide>
      <PageTitle
        aside={
          <span className={cn("ml-1 flex items-center gap-2 text-sm", connected ? "text-ok" : "text-muted-foreground")} title={connected ? "New sessions appear as they arrive." : "Reconnecting…"}>
            <Radio className="size-3.5" /> {connected ? "Live" : "Offline"}
          </span>
        }
      >
        Sessions
      </PageTitle>
      <Sub>
        Every voice call and text chat your agents had, newest first. They arrive from your platforms with no code (
        <HrefLink href={settingsHref(ws, "platforms")}>Settings, under Platforms</HrefLink>). You can also send them from{" "}
        {plats.has("livekit") && (
          <>
            the <HrefLink href="https://github.com/amanmibra/sopops/blob/main/docs/connect.md#connect-livekit">LiveKit helper</HrefLink>,{" "}
          </>
        )}
        the API or <code className="text-sm">sopops import</code>, with a token from <HrefLink href={settingsHref(ws, "access")}>Settings, under Access</HrefLink>{" "}
        (the gear icon).
      </Sub>

      <div className="mb-4 flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
        <FilterSelect label="Agent" value={filters.agent} options={agents.map((a) => [a, a])} onChange={(agent) => set({ agent })} />
        <FilterSelect label="Channel" value={filters.channel} options={[["voice", "Voice"], ["text", "Text"]]} onChange={(channel) => set({ channel })} />
        <FilterSelect label="Status" value={filters.status} options={(Object.keys(STATUS) as Status[]).map((s) => [s, s])} onChange={(status) => set({ status })} />
        <FilterSelect label="Result" value={filters.result} options={(Object.keys(RESULT) as Result[]).map((r) => [r, RESULT[r].label])} onChange={(result) => set({ result })} />
        <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-sm">
          <label className="flex items-center gap-2">
            From
            <Input type="datetime-local" className="h-8 w-auto" value={filters.since ?? ""} onChange={(e) => set({ since: e.target.value || undefined })} />
          </label>
          <label className="flex items-center gap-2">
            to
            <Input type="datetime-local" className="h-8 w-auto" value={filters.until ?? ""} onChange={(e) => set({ until: e.target.value || undefined })} />
          </label>
        </div>
        {filtered && (
          <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={() => onFilters({})}>
            Clear
          </Button>
        )}
      </div>

      <DrillChips filters={filters} onFilters={onFilters} />
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <Segmented
          small
          label={focus ? "Procedure result" : "Result"}
          value={outcome}
          options={[["all", "All"], ["followed", "Followed"], ["partial", "Partial"], ["deviated", "Deviated"], ["not_measurable", "Not measurable"]]}
          onChange={(v) => set({ [outcomeKey]: v === "all" ? undefined : v })}
        />
        <span className="text-muted-foreground text-xs">{focus ? "This procedure's result on each session." : "Each session's worst procedure result."} Select a row to preview it.</span>
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
                {["Started", "Agent", "Channel", "Duration", "Turns", "Release", ...(showStatus ? ["Status"] : []), "Result", ...(focus ? ["Rules"] : [])].map((h) => (
                  <th key={h} className={cn("text-muted-foreground border-b px-2 py-2 text-left text-xs font-medium whitespace-nowrap", NUMERIC.has(h) && "text-right")}>
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
                  onClick={() => setOpenId(s.id)}
                  aria-selected={openId === s.id}
                >
                  <td className="border-b px-2 py-2 whitespace-nowrap">
                    <HrefLink href={wsHref(ws, "sessions", s.id)} className="text-foreground" onClick={(e) => e.stopPropagation()}>
                      {when(s.started_at)}
                    </HrefLink>
                  </td>
                  <td className="border-b px-2 py-2 whitespace-nowrap">
                    <span className={cn(!s.agent_id && "text-muted-foreground")} title={s.agent_id ? undefined : "Not matched to an agent in sopops"}>
                      {agentName(s)}
                    </span>
                    <div className="text-muted-foreground text-xs"><PlatformLabel platform={s.platform} size={12} /></div>
                  </td>
                  <td className="border-b px-2 py-2 capitalize">{s.channel}</td>
                  <td className="border-b px-2 py-2 text-right tabular-nums">{clock(s.duration_s) || <span className="text-muted-foreground">—</span>}</td>
                  <td className="border-b px-2 py-2 text-right tabular-nums">{s.turn_count}</td>
                  <td className="border-b px-2 py-2 text-right tabular-nums">{s.release_id != null ? `#${s.release_id}` : <span className="text-muted-foreground">—</span>}</td>
                  {showStatus && (
                    <td className="border-b px-2 py-2">
                      {/* Checked is the normal end state, so only the others are marked. */}
                      {s.status !== "checked" && <StatusTag status={s.status} />}
                    </td>
                  )}
                  <td className="border-b px-2 py-2">
                    <ResultTag result={focus ? s.procedure_check?.result : s.result} />
                  </td>
                  {focus && (
                    <td className="border-b px-2 py-2">
                      <RuleDots rules={s.procedure_check?.rules ?? []} />
                    </td>
                  )}
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
      <SessionDrawer id={openId} ids={ids} onOpen={setOpenId} onExpand={(id) => go(wsHref(ws, "sessions", id))} />
    </Page>
  );
}

/** One dot per rule of the filtered procedure, colored by what happened on this session. */
function RuleDots({ rules }: { rules: { rule_id: string; kind: string; index: number; status: string }[] }) {
  if (!rules.length) return <span className="text-muted-foreground">—</span>;
  return (
    <Hint
      focusable={false}
      tip={
        <span className="grid grid-cols-[auto_1fr] gap-x-2">
          {rules.map((r) => (
            <span key={r.rule_id} className="contents">
              <b>{ruleName(r.kind, r.index)}</b>
              <span className="opacity-80">{STATUS_MARK[r.status]?.label ?? r.status}</span>
            </span>
          ))}
        </span>
      }
    >
      <span role="img" aria-label={rules.map((r) => `${ruleName(r.kind, r.index)} ${STATUS_MARK[r.status]?.label ?? r.status}`).join(", ")} className="flex items-center gap-[3px]">
        {rules.map((r) => {
          const m = STATUS_MARK[r.status];
          return <span key={r.rule_id} className={cn("inline-block size-2 rounded-full", m?.striped && "stripes border", r.kind === "warning" && "rounded-none")} style={m?.striped ? undefined : { background: m?.color ?? "var(--o-none)" }} />;
        })}
      </span>
    </Hint>
  );
}

/** A session's checks and transcript in a side panel, with previous / next through the list and a way to the full page. */
function SessionDrawer({ id, ids, onOpen, onExpand }: { id: string | null; ids: string[]; onOpen: (id: string | null) => void; onExpand: (id: string) => void }) {
  const { ws } = useWs();
  const res = useQuery({ ...q.session(ws, id ?? ""), enabled: !!id });
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const marks = useMemo(() => turnMarks(res.data?.checks ?? []), [res.data]);
  const at = id ? ids.indexOf(id) : -1;
  const s = res.data;
  useEffect(() => setActiveKey(null), [id]);
  const select = (key: string | null, turn?: number | null) => {
    setActiveKey(key);
    if (key && turn != null) document.getElementById(`turn-${turn}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onOpen(null)}>
      <SheetContent
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "j") at >= 0 && at < ids.length - 1 && onOpen(ids[at + 1]);
          if (e.key === "ArrowUp" || e.key === "k") at > 0 && onOpen(ids[at - 1]);
        }}
      >
        <div className="flex items-center gap-1 border-b px-3 py-2">
          <Button size="icon-sm" variant="ghost" aria-label="Previous session" disabled={at <= 0} onClick={() => onOpen(ids[at - 1])}>
            <ChevronLeft />
          </Button>
          <Button size="icon-sm" variant="ghost" aria-label="Next session" disabled={at < 0 || at >= ids.length - 1} onClick={() => onOpen(ids[at + 1])}>
            <ChevronRight />
          </Button>
          <span className="text-muted-foreground text-xs tabular-nums">{at >= 0 ? `${at + 1} of ${ids.length}` : ""}</span>
          <span className="flex-1" />
          {id && (
            <Button size="sm" variant="ghost" onClick={() => onExpand(id)}>
              <Maximize2 /> Full page
            </Button>
          )}
          <SheetClose asChild>
            <Button size="icon-sm" variant="ghost" aria-label="Close">
              <X />
            </Button>
          </SheetClose>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {res.isPending ? (
            <p className="text-muted-foreground">Loading…</p>
          ) : res.isError ? (
            <ErrorBanner error={res.error} />
          ) : s ? (
            <>
              <SheetTitle className="text-lg font-semibold">
                {agentName(s)}, {when(s.started_at)}
              </SheetTitle>
              <SheetDescription className="text-muted-foreground mt-0.5 mb-3 flex flex-wrap items-center gap-2 text-sm">
                <StatusTag status={s.status} />
                {s.result && <ResultTag result={s.result} />}
                <span>
                  {plural(s.turn_count, "turn", "turns")}
                  {s.duration_s != null ? `, ${clock(s.duration_s)} long` : ""}
                  {s.release_id != null ? `, release #${s.release_id}` : ""}
                </span>
              </SheetDescription>
              <h3 className="mb-2 text-sm font-semibold">Procedures</h3>
              <ChecksPanel checks={s.checks} activeKey={activeKey} onSelect={select} />
              <h3 className="mt-4 mb-1 text-sm font-semibold">Transcript</h3>
              <ol>
                {s.turns.map((t, i) => (
                  <Turn key={i} index={i} turn={t} marks={marks.get(i)} activeKey={activeKey} onSelect={(k) => select(k)} />
                ))}
              </ol>
            </>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}

// --- one session ----------------------------------------------------------------

const ROLE: Record<SessionTurn["role"], { label: string; className: string }> = {
  agent: { label: "Agent", className: "text-primary font-semibold" },
  user: { label: "User", className: "text-foreground font-semibold" },
  tool: { label: "Tool", className: "text-muted-foreground" },
  system: { label: "System", className: "text-muted-foreground" },
};

function Turn({ turn, index, marks, activeKey, onSelect }: { turn: SessionTurn; index: number; marks?: ReturnType<TurnMarks["get"]>; activeKey: string | null; onSelect: (key: string | null) => void }) {
  const r = ROLE[turn.role];
  const extra = turn as SessionTurn & { name?: string; interrupted?: boolean };
  const quiet = turn.role === "tool" || turn.role === "system";
  const active = marks?.find((m) => m.key === activeKey);
  const finding = marks?.some((m) => m.finding);
  const quotes = active ? active.quotes : (marks ?? []).flatMap((m) => m.quotes);
  return (
    <li
      id={`turn-${index}`}
      className={cn(
        "grid scroll-mt-24 grid-cols-[52px_64px_minmax(0,1fr)] gap-3 border-b border-l-2 border-l-transparent py-2 pl-2 last:border-b-0",
        quiet && "text-sm",
        finding && "border-l-err",
        active && "bg-muted",
      )}
    >
      <span className="text-muted-foreground pt-px font-mono text-xs" title={`Turn ${index}`}>
        {clock(turn.offset_s)}
      </span>
      <span className={cn("text-sm", r.className)}>{r.label}</span>
      <span className={cn("whitespace-pre-wrap", turn.role === "tool" && "font-mono text-muted-foreground", turn.role === "system" && "italic text-muted-foreground")}>
        {turn.text ? <Highlighted text={turn.text} quotes={quotes} strong={!!active} /> : extra.name ? extra.name : <span className="text-muted-foreground">(no text)</span>}
        {extra.interrupted && <Tag className="ml-2 align-middle">interrupted</Tag>}
        {marks && marks.length > 0 && (
          <span className="mt-1 flex flex-wrap gap-1">
            {marks.map((m) => (
              <button key={m.key} type="button" className="min-w-0 max-w-full" onClick={() => onSelect(m.key === activeKey ? null : m.key)} title="Show this rule">
                <Tag className={cn("cursor-pointer", m.finding ? "bg-err-soft text-err border-transparent" : "", m.key === activeKey && "ring-ring ring-1")}>
                  {m.label} · {m.status.replace("_", " ")}
                </Tag>
              </button>
            ))}
          </span>
        )}
      </span>
    </li>
  );
}


/** Where to open a session: a rule's finding, and the turn to scroll to. */
export type SessionAt = { procedure?: string; rule?: string; turn?: number; finding?: string };

export function SessionView({ id, at }: { id: string; at?: SessionAt }) {
  const { ws, releases } = useWs();
  useSessionStream(ws);
  const res = useQuery(q.session(ws, id));
  const [activeKey, setActiveKey] = useState<string | null>(at?.procedure && at.rule ? `${at.procedure}/${at.rule}` : null);
  const [scrollTo, setScrollTo] = useState<number | null>(null);
  const marks = useMemo(() => turnMarks(res.data?.checks ?? []), [res.data]);
  // Opened at a finding: once the session loads, scroll to its turn (or the rule's first evidence).
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current || !res.data || !at?.procedure || !at.rule) return;
    opened.current = true;
    const rule = res.data.checks.find((c) => c.procedure_id === at.procedure)?.rules.find((r) => r.rule_id === at.rule);
    const turn = at.turn ?? rule?.evidence.find((e) => e.turn != null)?.turn;
    if (turn != null) setScrollTo(turn);
  }, [res.data, at]);
  useEffect(() => {
    if (scrollTo == null) return;
    document.getElementById(`turn-${scrollTo}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    setScrollTo(null);
  }, [scrollTo]);
  const select = (key: string | null, turn?: number | null) => {
    setActiveKey(key);
    if (key && turn != null) setScrollTo(turn);
  };
  if (res.isPending) return <Page><p className="text-muted-foreground">Loading…</p></Page>;
  if (res.isError) return <Page><ErrorBanner error={res.error} /><HrefLink href={wsHref(ws, "sessions")}>All sessions</HrefLink></Page>;
  const s: SessionDetail = res.data;
  const release = s.release_id != null ? releases.find((r) => r.number === s.release_id) : undefined;
  const config = Object.entries(s.config ?? {}); // what it ran with: scalars inline, objects and arrays as JSON
  const facts: [string, React.ReactNode][] = [
    ["Agent", s.agent_id ? <HrefLink href={wsHref(ws, "agents", s.agent_id)}>{s.agent_id}</HrefLink> : (s.agent_ref ?? "—")],
    ["Release", s.release_id != null ? <HrefLink href={wsHref(ws, "releases", s.release_id)}>#{s.release_id}{release?.note ? `: ${release.note}` : ""}</HrefLink> : "—"],
    ["Platform version", s.version_ref ?? "—"],
    ["Platform", `${s.platform} (${s.channel})`],
    ["Platform id", <code className="text-sm break-all">{s.external_id}</code>],
    ["Started", when(s.started_at)],
    ["Ended", s.ended_at ? `${when(s.ended_at)} (${clock(s.duration_s)})` : "—"],
    ["Processed", s.processed_at ? when(s.processed_at) : "Not yet"],
    ["Model cost", s.model_calls ? <SessionCost calls={s.model_calls} cost={s.cost_usd} /> : "No model calls yet"],
  ];
  if (s.agent_ref && s.agent_id && s.agent_ref !== s.agent_id) facts.splice(1, 0, ["Sent as", s.agent_ref]);
  return (
    <Page wide>
      <div className="text-muted-foreground mb-2 text-sm">
        <HrefLink href={wsHref(ws, "sessions")}>Sessions</HrefLink> /
      </div>
      <PageTitle
        aside={
          <span className="flex items-center gap-2">
            <StatusTag status={s.status} big />
            {s.result && <ResultTag result={s.result} big />}
          </span>
        }
      >
        {agentName(s)}, {when(s.started_at)}
      </PageTitle>
      <Sub>
        {plural(s.turn_count, "turn", "turns")}
        {s.tool_call_count ? `, ${plural(s.tool_call_count, "tool call", "tool calls")}` : ""}
        {s.duration_s != null ? `, ${clock(s.duration_s)} long` : ""}
        {s.model_calls ? (
          <>
            , <SessionCost calls={s.model_calls} cost={s.cost_usd} />
          </>
        ) : null}
        .
      </Sub>
      {s.status_reason && <Banner tone={s.status === "failed" ? "err" : "warn"}>{s.status_reason}</Banner>}
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_400px]">
        <Panel title="Transcript" className="min-w-0">
          {s.turns.length ? (
            <ol>
              {s.turns.map((t, i) => (
                <Turn key={i} index={i} turn={t} marks={marks.get(i)} activeKey={activeKey} onSelect={(k) => select(k)} />
              ))}
            </ol>
          ) : (
            <Note>No turns.</Note>
          )}
        </Panel>
        <div className="min-w-0">
          <Panel title="Procedures">
            <ChecksPanel checks={s.checks} activeKey={activeKey} onSelect={select} />
          </Panel>
          <Panel title="Details">
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
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
              <ul className="grid grid-cols-[minmax(0,1fr)] gap-3">
                {s.tool_calls.map((t, i) => (
                  <li key={i} className="min-w-0 text-sm">
                    <div className="font-mono font-semibold break-all">
                      {String(t.name ?? "tool")}
                      {t.is_error ? <Tag className="bg-err-soft text-err ml-2 border-transparent">error</Tag> : null}
                    </div>
                    {t.arguments != null && <JsonBlock value={t.arguments} className="mt-1" />}
                    {t.result != null && <JsonBlock value={t.result} prefix="→ " tone={t.is_error ? "err" : undefined} className="mt-1" />}
                  </li>
                ))}
              </ul>
            </Panel>
          )}
          <Panel title="Settings">
            {config.length ? (
              <>
                <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
                  {config.map(([k, v]) => (
                    <div key={k} className="contents">
                      <dt className="text-muted-foreground break-all">{k}</dt>
                      <dd className="min-w-0 break-all">
                        <JsonInline value={v} />
                      </dd>
                    </div>
                  ))}
                </dl>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <JsonDialog value={s.config} title="What this session ran with" subtitle={s.config_hash ? `Version ${s.config_hash}: sessions with the same agent, release and config are one version.` : "What the agent ran with, as sopops stored it."} />
                  {s.config_hash && <span className="text-muted-foreground font-mono text-xs">{s.config_hash}</span>}
                </div>
              </>
            ) : (
              <Note>Nothing reported.</Note>
            )}
          </Panel>
          <Panel title="Metadata">
            {Object.keys(s.metadata).length ? (
              <JsonBlock value={s.metadata} />
            ) : (
              <Note>None.</Note>
            )}
          </Panel>
        </div>
      </div>
    </Page>
  );
}
