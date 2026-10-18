// The frame: the top bar (workspace, QA or Instructions, author, unpublished changes, settings, theme) and the
// workspace's side rail, which lists the pages of the mode you're in.
import { useQuery } from "@tanstack/react-query";
import { useLocation, useParams } from "@tanstack/react-router";
import { Monitor, Moon, Plus, Settings, Sun } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { q } from "@/lib/api";
import { rq } from "@/lib/review";
import { KIND, type ListKind, WORKSPACE_ID } from "@/lib/kinds";
import { MODES, type Mode, currentMode, modeHref } from "@/lib/mode";
import { prefs } from "@/lib/prefs";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { settingsHref, useWs, wsHref } from "@/lib/workspace";
import { Tag } from "./bits";
import { HrefLink, useGo } from "./href-link";
import { Logo } from "./logo";
import { PublishButton } from "./publish";
import { typing, useNeedsReview } from "./review";
import { Button } from "./ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "./ui/dropdown-menu";
import { Input } from "./ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { ToggleGroup, ToggleGroupItem } from "./ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

/** The mode of the page you're on (Settings and the workspace root keep the one you came from). */
export function useMode(ws: string): Mode {
  const { pathname, search } = useLocation();
  return currentMode(ws, pathname, (search as Record<string, unknown>).from);
}

/** QA | Instructions. Each goes back to the last page you had open in it. Keys: g q, g i. */
function ModeSwitch({ ws, mode }: { ws: string; mode: Mode }) {
  const go = useGo();
  const armed = useRef(0);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return;
      if (e.key === "g") {
        armed.current = Date.now();
        return;
      }
      const to = e.key === "q" ? "qa" : e.key === "i" ? "instructions" : null;
      if (to && Date.now() - armed.current < 1500) {
        e.preventDefault();
        go(modeHref(ws, to));
      }
      armed.current = 0;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ws, go]);
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      size="sm"
      value={mode}
      onValueChange={(v) => v && go(modeHref(ws, v as Mode))}
      aria-label="Mode"
      className="bg-card max-md:w-full"
    >
      {MODES.map((m) => (
        <Tooltip key={m.mode}>
          <TooltipTrigger asChild>
            <ToggleGroupItem
              value={m.mode}
              className="text-muted-foreground aria-checked:bg-brand-soft aria-checked:text-foreground aria-checked:hover:bg-brand-soft px-3.5 font-normal aria-checked:font-semibold max-md:flex-1"
            >
              {m.label}
            </ToggleGroupItem>
          </TooltipTrigger>
          <TooltipContent sideOffset={6}>
            {m.tip} <span className="opacity-70">(g {m.mode === "qa" ? "q" : "i"})</span>
          </TooltipContent>
        </Tooltip>
      ))}
    </ToggleGroup>
  );
}

/** Workspace-wide settings (tokens, GitHub repository, platforms, defaults), behind a gear. */
function SettingsLink({ ws, mode }: { ws: string; mode: Mode }) {
  const { pathname } = useLocation();
  const draft = useQuery(q.draft(ws));
  const changed = draft.data?.items.find((i) => i.kind === "settings");
  const here = routeIsSettings(pathname);
  return (
    <Button asChild variant="ghost" size="icon-sm" className={cn("relative", here && "bg-brand-soft hover:bg-brand-soft")}>
      <HrefLink href={`${settingsHref(ws)}?from=${mode}`} aria-label="Settings" aria-current={here ? "page" : undefined} title="Settings: tokens, GitHub, platforms, defaults" className="text-foreground hover:no-underline">
        <Settings />
        {changed && <span className={cn("absolute top-1 right-1 size-[7px] rounded-full", changed.change === "deleted" ? "bg-err" : "bg-warn")} aria-label={`settings ${changed.change}, not published`} />}
      </HrefLink>
    </Button>
  );
}

const routeIsSettings = (pathname: string) => /^\/w\/[^/]+\/settings(\/|$)/.test(pathname);

function ThemeMenu() {
  const { theme, resolved, setTheme } = useTheme();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label="Theme" title="Light or dark">
          {resolved === "dark" ? <Moon /> : <Sun />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup value={theme} onValueChange={(v) => setTheme(v as typeof theme)}>
          <DropdownMenuRadioItem value="light">
            <Sun className="size-4" /> Light
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">
            <Moon className="size-4" /> Dark
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system">
            <Monitor className="size-4" /> System
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function NewWorkspace({ onDone }: { onDone: () => void }) {
  const go = useGo();
  const [name, setName] = useState("");
  const [bad, setBad] = useState(false);
  const create = () => {
    const n = name.trim();
    if (!WORKSPACE_ID.test(n)) return setBad(true);
    onDone();
    go(wsHref(n));
  };
  return (
    <span className="flex items-center gap-1.5">
      <Input
        autoFocus
        className={cn("h-8 w-44", bad && "border-err")}
        placeholder="Workspace name"
        aria-label="New workspace name"
        title={bad ? "Use letters, digits, '.', '_' and '-'." : undefined}
        value={name}
        onChange={(e) => {
          setName(e.target.value);
          setBad(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") create();
          if (e.key === "Escape") onDone();
        }}
      />
      <Button size="sm" variant="outline" onClick={create}>
        Create
      </Button>
      <Button size="sm" variant="ghost" onClick={onDone}>
        Cancel
      </Button>
    </span>
  );
}

export function TopBar() {
  const { ws } = useParams({ strict: false }) as { ws?: string };
  return ws ? <WsTopBar ws={ws} /> : <Bar />;
}

function WsTopBar({ ws }: { ws: string }) {
  const { pathname } = useLocation();
  const mode = useMode(ws);
  const legacy = useQuery(q.config(ws)).data?.legacy;
  // Publishing is about instructions; Settings edits are drafts too, so it's there as well.
  const publish = mode === "instructions" || routeIsSettings(pathname);
  return (
    <Bar
      ws={ws}
      mode={!legacy && <ModeSwitch ws={ws} mode={mode} />}
      end={
        <>
          {publish && <PublishButton ws={ws} />}
          <SettingsLink ws={ws} mode={mode} />
        </>
      }
    />
  );
}

function Bar({ ws, mode, end }: { ws?: string; mode?: ReactNode; end?: ReactNode }) {
  const go = useGo();
  const workspaces = useQuery(q.workspaces());
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState(() => prefs.get("author") ?? "");
  const names = (workspaces.data ?? []).map((w) => w.workspace);
  if (ws && !names.includes(ws)) names.push(ws);
  return (
    <header className="bg-card sticky top-0 z-20 flex flex-wrap items-center gap-2.5 border-b px-4 py-2.5 md:px-5">
      <HrefLink href="/" className="text-foreground mr-1.5 flex items-center gap-2 text-[15px] font-semibold tracking-tight hover:no-underline">
        <Logo />
        sopops
      </HrefLink>
      <Select value={ws ?? ""} onValueChange={(v) => go(wsHref(v))}>
        <SelectTrigger size="sm" className="min-w-40" aria-label="Workspace">
          <SelectValue placeholder={names.length ? "Pick a workspace" : "No workspaces yet"} />
        </SelectTrigger>
        <SelectContent>
          {names.map((w) => (
            <SelectItem key={w} value={w}>
              {w}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {adding ? (
        <NewWorkspace onDone={() => setAdding(false)} />
      ) : ws ? (
        <Button size="icon-sm" variant="ghost" className="text-muted-foreground" aria-label="New workspace" title="New workspace" onClick={() => setAdding(true)}>
          <Plus />
        </Button>
      ) : (
        <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={() => setAdding(true)}>
          <Plus /> New workspace
        </Button>
      )}
      {mode && <div className="max-md:order-last max-md:w-full md:ml-2">{mode}</div>}
      <span className="flex-1" />
      <Input
        className="h-8 w-24 md:w-36"
        placeholder="Your name"
        aria-label="Your name, recorded with changes"
        value={name}
        onChange={(e) => {
          setName(e.target.value);
          prefs.set("author", e.target.value.trim());
        }}
      />
      {end}
      <ThemeMenu />
    </header>
  );
}

function RailLink({ href, children, extra, change }: { href: string; children: ReactNode; extra?: ReactNode; change?: string }) {
  const { pathname } = useLocation();
  const here = decodeURIComponent(pathname);
  const target = decodeURIComponent(href);
  const active = here === target || here.startsWith(target + "/");
  return (
    <HrefLink
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn("text-foreground flex min-w-0 items-center gap-2 rounded-md px-2 py-1 hover:bg-muted hover:no-underline", active && "bg-brand-soft hover:bg-brand-soft")}
    >
      <span className="flex-1 truncate">{children}</span>
      {extra}
      {change && <span className={cn("size-[7px] shrink-0 rounded-full", change === "deleted" ? "bg-err" : "bg-warn")} title={`${change}, not published`} />}
    </HrefLink>
  );
}

function CountBadge({ n, title }: { n: number | null; title: string }) {
  if (!n) return null;
  return (
    <span className="bg-warn-soft text-warn shrink-0 rounded-full px-1.5 text-[11px] font-medium tabular-nums" title={title} aria-label={`${n} ${title.toLowerCase()}`}>
      {n}
    </span>
  );
}

export function Rail() {
  const { ws, cfg, legacy, draft, href, changeOf } = useWs();
  const go = useGo();
  const mode = useMode(ws);
  const needsReview = useNeedsReview(ws);
  const proposed = useQuery({ ...rq.suggestions(ws, "proposed"), retry: false, enabled: !legacy }).data?.items.length ?? null;
  const group = (kind: ListKind, items: { id: string; label: string }[]) => (
    <div className="mb-[18px]" key={kind}>
      <div className="flex items-center justify-between px-2 pb-1">
        <h2 className="text-muted-foreground text-[13px] font-semibold">
          <HrefLink href={href(kind)} className="text-inherit hover:no-underline">
            {KIND[kind].many}
          </HrefLink>
        </h2>
        <Button size="xs" variant="ghost" className="text-muted-foreground" aria-label={`New ${KIND[kind].one.toLowerCase()}`} onClick={() => go(href(kind, "new"))}>
          + New
        </Button>
      </div>
      {items.length ? (
        items.map((it) => (
          <RailLink key={it.id} href={href(kind, it.id)} change={changeOf(kind, it.id)?.change}>
            {it.label}
          </RailLink>
        ))
      ) : (
        <div className="text-muted-foreground px-2 py-0.5 text-[13px]">None yet</div>
      )}
    </div>
  );
  const releases = (
    <RailLink href={wsHref(ws, "releases")} extra={draft.release ? <Tag tone="live">#{draft.release} live</Tag> : undefined}>
      Releases
    </RailLink>
  );
  return (
    <nav
      className="bg-card border-b px-2.5 pt-3.5 pb-10 md:sticky md:top-[53px] md:h-[calc(100vh-53px)] md:overflow-auto md:border-r md:border-b-0"
      aria-label={legacy ? "Workspace contents" : mode === "qa" ? "QA" : "Instructions"}
    >
      {legacy ? (
        <div className="mb-[18px]">{releases}</div>
      ) : mode === "qa" ? (
        <div className="mb-[18px]">
          <RailLink href={wsHref(ws, "insights")}>Insights</RailLink>
          <RailLink href={wsHref(ws, "sessions")}>Sessions</RailLink>
          <RailLink href={wsHref(ws, "patterns")} extra={<CountBadge n={needsReview} title="Patterns to review: no verdict yet, or back after a fix" />}>
            Patterns
          </RailLink>
          <RailLink href={wsHref(ws, "suggestions")} extra={<CountBadge n={proposed} title="Suggestions waiting for a decision" />}>
            Suggestions
          </RailLink>
          <RailLink href={wsHref(ws, "report")}>Report</RailLink>
        </div>
      ) : (
        <>
          <div className="mb-[18px]">
            <RailLink href={wsHref(ws, "overview")}>Overview</RailLink>
          </div>
          {group("agent", cfg.agents.map((a) => ({ id: a.id, label: a.id })))}
          {group("instruction", cfg.instructions.map((a) => ({ id: a.id, label: a.id })))}
          {group("procedure", [...cfg.procedures].sort((a, b) => a.name.localeCompare(b.name)).map((p) => ({ id: p.id, label: p.name })))}
          {group("group", cfg.groups.map((a) => ({ id: a.id, label: a.id })))}
          <div className="mb-[18px]">
            <RailLink href={wsHref(ws, "changes")} extra={<CountBadge n={draft.items.length} title="Unpublished changes" />}>
              Changes
            </RailLink>
            {releases}
            <RailLink href={wsHref(ws, "import")}>Import</RailLink>
          </div>
        </>
      )}
    </nav>
  );
}
