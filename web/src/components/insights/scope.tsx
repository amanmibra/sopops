// The Insights frame: title, the view tabs with the scope bar level with them (window presets or a custom range,
// procedure, agent, release, channel), and a live "as of" line. Every filter lives in the URL.
import { CalendarRange, RefreshCw } from "lucide-react";
import { type ReactNode, useState } from "react";
import { type InsightFilters, WINDOWS } from "@/lib/api";
import { WINDOW_LABEL, WINDOW_SHORT } from "@/lib/insights";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";

const ANY = "any";

export function Pick({ label, value, options, onChange, className }: { label: string; value: string | undefined; options: [string, string][]; onChange: (v: string | undefined) => void; className?: string }) {
  return (
    <Select value={value ?? ANY} onValueChange={(v) => onChange(v === ANY ? undefined : v)}>
      <SelectTrigger size="sm" className={cn("bg-card min-w-32", className)} aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ANY}>{`All ${label.toLowerCase()}s`}</SelectItem>
        {options.map(([v, l]) => (
          <SelectItem key={v} value={v}>
            {l}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

const today = () => {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

/** Presets end at the moment each request runs (so a tab left open stays current); Custom takes two days. */
function WindowPicker({ filters, onChange }: { filters: InsightFilters; onChange: (patch: Partial<InsightFilters>) => void }) {
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState(filters.start ?? "");
  const [end, setEnd] = useState(filters.end ?? "");
  const current = filters.start ? "custom" : (filters.window ?? "7d");
  const btn = (active: boolean) => cn("cursor-pointer border-r border-border px-2 py-1 text-sm last:border-r-0", active ? "bg-brand-soft text-foreground font-semibold" : "text-muted-foreground hover:bg-muted hover:text-foreground");
  const bad = !start || (end && end < start) || start > today();
  return (
    <div role="group" aria-label="Time range" className="bg-card inline-flex h-8 items-stretch overflow-hidden rounded-md border border-input">
      {WINDOWS.map((w) => (
        <button key={w} type="button" aria-pressed={current === w} title={`The last ${WINDOW_LABEL[w]}, up to now`} className={btn(current === w)} onClick={() => onChange({ window: w === "7d" ? undefined : w, start: undefined, end: undefined })}>
          {WINDOW_SHORT[w]}
        </button>
      ))}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button type="button" aria-pressed={current === "custom"} className={cn(btn(current === "custom"), "inline-flex items-center gap-2")}>
            <CalendarRange className="size-3.5" /> Custom
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-72">
          <form
            className="grid gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (bad) return;
              onChange({ window: undefined, start, end: end || undefined });
              setOpen(false);
            }}
          >
            <div className="text-sm font-semibold">Custom time range</div>
            <label className="text-muted-foreground grid gap-1 text-xs">
              From
              <Input type="date" max={today()} value={start} onChange={(e) => setStart(e.target.value)} required />
            </label>
            <label className="text-muted-foreground grid gap-1 text-xs">
              To (leave empty to run up to now)
              <Input type="date" max={today()} min={start || undefined} value={end} onChange={(e) => setEnd(e.target.value)} />
            </label>
            <p className="text-muted-foreground text-xs">Up to 366 days, both days included. Every number is compared with the same length of time just before.</p>
            <Button size="sm" type="submit" disabled={!!bad}>
              Apply
            </Button>
          </form>
        </PopoverContent>
      </Popover>
    </div>
  );
}

/** Window, procedure, agent, release and channel: the scope of every number on the page. */
export function ScopeBar({ filters, onChange, procedure = true, release = true }: { filters: InsightFilters; onChange: (f: InsightFilters) => void; procedure?: boolean; release?: boolean }) {
  const { cfg, releases } = useWs();
  const set = (patch: Partial<InsightFilters>) => onChange({ ...filters, ...patch });
  const agents = [...new Set([...cfg.agents.map((a) => a.id), ...(filters.agent ? [filters.agent] : [])])];
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Scope">
      <WindowPicker filters={filters} onChange={set} />
      {procedure && <Pick label="Procedure" value={filters.procedure} options={cfg.procedures.map((p) => [p.id, p.name])} onChange={(procedure) => set({ procedure })} />}
      <Pick label="Agent" value={filters.agent} options={agents.map((a) => [a, a])} onChange={(agent) => set({ agent })} />
      {release && (
        <Pick
          label="Release"
          value={filters.release?.[0] != null ? String(filters.release[0]) : undefined}
          options={[...releases].sort((a, b) => b.number - a.number).map((r) => [String(r.number), `#${r.number}${r.note ? ` ${r.note}` : ""}`])}
          onChange={(v) => set({ release: v ? [Number(v)] : undefined })}
          className="max-w-56"
        />
      )}
      <Pick label="Channel" value={filters.channel} options={[["voice", "Voice"], ["text", "Text"]]} onChange={(channel) => set({ channel: channel as InsightFilters["channel"] })} />
    </div>
  );
}

export type View = "overview" | "releases" | "versions" | "report";

/** "As of 14:02 · refresh": presets end at now, and the page refetches every minute and on focus. */
export function AsOf({ at, fetching, onRefresh }: { at: number; fetching: boolean; onRefresh: () => void }) {
  if (!at) return null;
  return (
    <span className="text-muted-foreground inline-flex items-center gap-2 text-xs">
      As of {new Date(at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
      <Button variant="ghost" size="icon-xs" aria-label="Refresh" title="Refresh" onClick={onRefresh} disabled={fetching}>
        <RefreshCw className={cn(fetching && "animate-spin")} />
      </Button>
    </span>
  );
}

/** The top of every Insights view: title and subtitle, then the view tabs with the scope bar on the same row. */
export function InsightsShell({
  view: _view,
  title,
  subtitle,
  filters,
  onFilters,
  scope,
  status,
  children,
}: {
  view: View;
  title: string;
  subtitle: ReactNode;
  filters: InsightFilters;
  onFilters: (f: InsightFilters) => void;
  scope?: { procedure?: boolean; release?: boolean };
  status?: ReactNode;
  children: ReactNode;
}) {
  return (
    // Insights is a working canvas: tables, comparisons and charts should use the
    // width that remains after the rail instead of stopping at a reading-column cap.
    <div className="w-full min-w-0">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold">{title}</h1>
          <p className="text-muted-foreground mt-1 text-sm">{subtitle}</p>
        </div>
        {status}
      </div>
      <div className="mb-4 flex flex-wrap justify-end gap-2">
        <ScopeBar filters={filters} onChange={onFilters} {...scope} />
      </div>
      {children}
    </div>
  );
}
