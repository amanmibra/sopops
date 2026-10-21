// The frame: the top bar (team, QA or Instructions, author or user menu, unpublished changes, settings, theme) and the
// workspace's side rail, which lists the pages of the mode you're in.
import { useQuery } from "@tanstack/react-query";
import { useLocation, useParams } from "@tanstack/react-router";
import {
  BookText,
  Bot,
  Boxes,
  ChartLine,
  ClipboardCheck,
  FilePenLine,
  FileText,
  Import,
  Layers,
  LayoutGrid,
  Lightbulb,
  ListOrdered,
  type LucideIcon,
  Menu,
  MessagesSquare,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  ScrollText,
  Settings,
  Sun,
  Tag as TagIcon,
} from "lucide-react";
import { type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { type AuthState, q } from "@/lib/api";
import { rq } from "@/lib/review";
import { KIND, type ListKind } from "@/lib/kinds";
import { MODES, type Mode, currentMode, modeHref } from "@/lib/mode";
import { prefs } from "@/lib/prefs";
import { setRailCollapsed, useRailCollapsed } from "@/lib/rail";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { settingsHref, useWs, wsHref } from "@/lib/workspace";
import { UserMenu } from "./account";
import { RailSetUp } from "./checklist";
import { Tag } from "./bits";
import { HrefLink, useGo } from "./href-link";
import { Logo } from "./logo";
import { PublishButton } from "./publish";
import { typing, useNeedsReview } from "./review";
import { Button } from "./ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "./ui/dropdown-menu";
import { Input } from "./ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Sheet, SheetContent, SheetTitle } from "./ui/sheet";
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
    <nav aria-label="Mode" className="flex items-center gap-1 max-md:w-full">
      {MODES.map((m) => {
        const Icon = MODE_ICON[m.mode];
        const here = m.mode === mode;
        return (
          <Tooltip key={m.mode}>
            <TooltipTrigger asChild>
              <Button
                size="sm"
                variant="ghost"
                aria-current={here ? "page" : undefined}
                onClick={() => go(modeHref(ws, m.mode))}
                className={cn("text-muted-foreground gap-1.5 px-3 font-normal max-md:flex-1", here && "bg-brand-soft text-foreground hover:bg-brand-soft font-semibold")}
              >
                <Icon className="size-4" />
                {m.label}
              </Button>
            </TooltipTrigger>
            <TooltipContent sideOffset={6}>
              {m.tip} <span className="opacity-70">(g {m.mode === "qa" ? "q" : "i"})</span>
            </TooltipContent>
          </Tooltip>
        );
      })}
    </nav>
  );
}

const MODE_ICON: Record<Mode, typeof ClipboardCheck> = { qa: ClipboardCheck, instructions: BookText };

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

// On phones the rail folds into a sheet; the top bar's menu button opens it. They render in different trees (the bar
// in the root, the rail in the workspace), so they share this one flag.
let railOpen = false;
const railListeners = new Set<() => void>();
const setRailOpen = (v: boolean) => {
  railOpen = v;
  railListeners.forEach((f) => f());
};
const useRailOpen = () =>
  useSyncExternalStore(
    (f) => {
      railListeners.add(f);
      return () => railListeners.delete(f);
    },
    () => railOpen,
  );

/** Opens the rail as a sheet on phones. */
function RailMenuButton() {
  const { pathname } = useLocation();
  if (routeIsSettings(pathname) || /^\/w\/[^/]+\/setup$/.test(pathname)) return null;
  return (
    <Button variant="ghost" size="icon-sm" className="-ml-1 md:hidden" aria-label="Menu" onClick={() => setRailOpen(true)}>
      <Menu />
    </Button>
  );
}

export function TopBar({ auth }: { auth: AuthState }) {
  const { ws } = useParams({ strict: false }) as { ws?: string };
  return ws ? <WsTopBar ws={ws} auth={auth} /> : <Bar auth={auth} />;
}

function WsTopBar({ ws, auth }: { ws: string; auth: AuthState }) {
  const mode = useMode(ws);
  const legacy = useQuery(q.config(ws)).data?.legacy;
  return (
    <Bar
      ws={ws}
      auth={auth}
      menu={<RailMenuButton />}
      mode={!legacy && <ModeSwitch ws={ws} mode={mode} />}
      end={
        <>
          <PublishButton ws={ws} />
          <SettingsLink ws={ws} mode={mode} />
        </>
      }
    />
  );
}

/**
 * The team's name (a team has one workspace, so there's nothing to pick). With sign-in off, an older install with
 * several workspaces keeps a picker, so none of them is out of reach.
 */
function Bar({ ws, auth, menu, mode, end }: { ws?: string; auth: AuthState; menu?: ReactNode; mode?: ReactNode; end?: ReactNode }) {
  const go = useGo();
  const workspaces = useQuery({ ...q.workspaces(), enabled: !auth.required });
  const [name, setName] = useState(() => prefs.get("author") ?? "");
  const names = (workspaces.data ?? []).map((w) => w.workspace);
  if (ws && names.length && !names.includes(ws)) names.push(ws);
  return (
    <header className="bg-card sticky top-0 z-20 flex flex-wrap items-center gap-x-1 gap-y-2 border-b px-3 py-2.5 sm:gap-2.5 sm:px-4 md:px-5">
      {menu}
      <HrefLink href="/" className="text-foreground flex items-center gap-2 text-[15px] sm:mr-1.5 font-semibold tracking-tight hover:no-underline">
        <Logo />
        <span className={cn(ws && "max-sm:sr-only")}>sopops</span>
      </HrefLink>
      {auth.org && <span className="text-muted-foreground max-w-48 truncate text-[13px]" title="Your team">{auth.org.name}</span>}
      {!auth.required && names.length > 1 && (
        <Select value={ws ?? ""} onValueChange={(v) => go(wsHref(v))}>
          <SelectTrigger size="sm" className="max-w-32 sm:max-w-none sm:min-w-40" aria-label="Workspace">
            <SelectValue placeholder="Pick one" />
          </SelectTrigger>
          <SelectContent>
            {names.map((w) => (
              <SelectItem key={w} value={w}>
                {w}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      {mode && <div className="max-md:order-last max-md:w-full md:ml-2">{mode}</div>}
      <span className="flex-1" />
      {!auth.user && (
        <Input
          className="h-8 w-36 max-md:hidden"
          placeholder="Your name"
          aria-label="Your name, recorded with changes"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            prefs.set("author", e.target.value.trim());
          }}
        />
      )}
      {end}
      <ThemeMenu />
      <UserMenu auth={auth} />
    </header>
  );
}

const useActive = (href: string) => {
  const here = decodeURIComponent(useLocation().pathname);
  const target = decodeURIComponent(href);
  return here === target || here.startsWith(target + "/");
};

const changeDot = (change: string, className?: string) => (
  <span className={cn("size-[7px] shrink-0 rounded-full", change === "deleted" ? "bg-err" : "bg-warn", className)} title={`${change}, not published`} />
);

/** One of a team's items in an expanded list (an agent, a procedure): a label, no icon, lined up with its heading's label. */
function RailLink({ href, children, change }: { href: string; children: ReactNode; change?: string }) {
  const active = useActive(href);
  return (
    <HrefLink
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn("text-foreground flex min-w-0 items-center gap-2 rounded-md py-1 pr-2 pl-8 hover:bg-muted hover:no-underline", active && "bg-brand-soft hover:bg-brand-soft")}
    >
      <span className="flex-1 truncate">{children}</span>
      {change && changeDot(change)}
    </HrefLink>
  );
}

function CountBadge({ n, title }: { n: number | null | undefined; title: string }) {
  if (!n) return null;
  return (
    <span className="bg-warn-soft text-warn shrink-0 rounded-full px-1.5 text-[11px] font-medium tabular-nums" title={title} aria-label={`${n} ${title.toLowerCase()}`}>
      {n}
    </span>
  );
}

/** A page in the rail: icon and label, or the icon alone (label in a tooltip) when the rail is collapsed. A count
 * becomes a small number on the icon. */
export function RailItem({
  href,
  icon: Icon,
  label,
  collapsed,
  active: activeProp,
  count,
  countTitle = "",
  change,
  extra,
  muted,
}: {
  href: string;
  icon: LucideIcon;
  label: string;
  collapsed?: boolean;
  active?: boolean;
  count?: number | null;
  countTitle?: string;
  change?: string;
  extra?: ReactNode;
  muted?: boolean;
}) {
  const matched = useActive(href);
  const active = activeProp ?? matched;
  const link = (
    <HrefLink
      href={href}
      aria-current={active ? "page" : undefined}
      aria-label={collapsed ? (count ? `${label}, ${count} ${countTitle.toLowerCase()}` : label) : undefined}
      className={cn(
        "relative flex min-w-0 items-center gap-2 rounded-md px-2 py-1 hover:bg-muted hover:no-underline",
        muted ? "text-muted-foreground text-[13px]" : "text-foreground",
        collapsed && "mx-auto size-9 justify-center p-0",
        active && "bg-brand-soft hover:bg-brand-soft",
      )}
    >
      <Icon className={cn("text-muted-foreground size-4 shrink-0", active && "text-foreground")} />
      {collapsed ? (
        count ? (
          <span className="bg-warn text-card absolute -top-1 -right-1 min-w-4 rounded-full px-1 text-center text-[11px] leading-4 font-semibold tabular-nums">{count}</span>
        ) : (
          change && changeDot(change, "absolute top-1 right-1")
        )
      ) : (
        <>
          <span className="flex-1 truncate">{label}</span>
          {extra}
          <CountBadge n={count} title={countTitle} />
          {change && changeDot(change)}
        </>
      )}
    </HrefLink>
  );
  if (!collapsed) return link;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      <TooltipContent side="right" sideOffset={8}>
        {label}
        {count ? ` (${count})` : ""}
      </TooltipContent>
    </Tooltip>
  );
}

/** The rail on wider screens: its pages, and the button that collapses or expands it pinned at the bottom. */
export function RailFrame({ label, collapsed, children }: { label: string; collapsed: boolean; children: ReactNode }) {
  const toggle = (
    <Button
      variant="ghost"
      size={collapsed ? "icon-sm" : "sm"}
      className={cn("text-muted-foreground font-normal", collapsed ? "mx-auto" : "w-full justify-start gap-2 px-2")}
      aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      onClick={() => setRailCollapsed(!collapsed)}
    >
      {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
      {!collapsed && "Collapse"}
    </Button>
  );
  return (
    <nav aria-label={label} className="bg-card sticky top-[53px] hidden h-[calc(100vh-53px)] flex-col border-r md:flex">
      <div className={cn("min-h-0 flex-1 overflow-auto pt-3.5 pb-4", collapsed ? "px-2.5" : "px-2.5")}>{children}</div>
      <div className="flex px-2.5 py-2">
        {collapsed ? (
          <Tooltip>
            <TooltipTrigger asChild>{toggle}</TooltipTrigger>
            <TooltipContent side="right" sideOffset={8}>
              Expand sidebar
            </TooltipContent>
          </Tooltip>
        ) : (
          toggle
        )}
      </div>
    </nav>
  );
}

const KIND_ICON: Record<ListKind, LucideIcon> = { agent: Bot, instruction: ScrollText, procedure: ListOrdered, group: Boxes };

export function Rail() {
  const { ws, cfg, legacy, draft, href, changeOf } = useWs();
  const go = useGo();
  const mode = useMode(ws);
  const needsReview = useNeedsReview(ws);
  const proposed = useQuery({ ...rq.suggestions(ws, "proposed"), retry: false, enabled: !legacy }).data?.items.length ?? null;
  const collapsed = useRailCollapsed();
  const label = legacy ? "Team files" : mode === "qa" ? "QA" : "Instructions";

  const body = (c: boolean) => {
    const group = (kind: ListKind, items: { id: string; label: string }[]) => {
      const KindIcon = KIND_ICON[kind];
      return c ? (
        <RailItem key={kind} collapsed href={href(kind)} icon={KindIcon} label={KIND[kind].many} />
      ) : (
        <div className="mb-[18px]" key={kind}>
          <div className="flex items-center justify-between pb-1 pl-2">
            <h2 className="text-muted-foreground text-[13px] font-semibold">
              <HrefLink href={href(kind)} className="flex items-center gap-2 text-inherit hover:no-underline">
                <KindIcon className="size-4 shrink-0" />
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
            <div className="text-muted-foreground py-0.5 pr-2 pl-8 text-[13px]">None yet</div>
          )}
        </div>
      );
    };
    const section = c ? "mb-3 grid gap-1" : "mb-[18px]";
    const releases = (
      <RailItem collapsed={c} href={wsHref(ws, "releases")} icon={TagIcon} label="Releases" extra={draft.release ? <Tag tone="live">#{draft.release} live</Tag> : undefined} />
    );
    return (
      <>
        {!legacy && !c && <RailSetUp />}
        {legacy ? (
          <div className={section}>{releases}</div>
        ) : mode === "qa" ? (
          <div className={section}>
            <RailItem collapsed={c} href={wsHref(ws, "insights")} icon={ChartLine} label="Insights" />
            <RailItem collapsed={c} href={wsHref(ws, "sessions")} icon={MessagesSquare} label="Sessions" />
            <RailItem collapsed={c} href={wsHref(ws, "patterns")} icon={Layers} label="Patterns" count={needsReview} countTitle="Patterns to review: no verdict yet, or back after a fix" />
            <RailItem collapsed={c} href={wsHref(ws, "suggestions")} icon={Lightbulb} label="Suggestions" count={proposed} countTitle="Suggestions waiting for a decision" />
            <RailItem collapsed={c} href={wsHref(ws, "report")} icon={FileText} label="Report" />
          </div>
        ) : (
          <>
            <div className={section}>
              <RailItem collapsed={c} href={wsHref(ws, "overview")} icon={LayoutGrid} label="Overview" />
            </div>
            <div className={c ? section : undefined}>
              {group("agent", cfg.agents.map((a) => ({ id: a.id, label: a.id })))}
              {group("instruction", cfg.instructions.map((a) => ({ id: a.id, label: a.id })))}
              {group("procedure", [...cfg.procedures].sort((a, b) => a.name.localeCompare(b.name)).map((p) => ({ id: p.id, label: p.name })))}
              {group("group", cfg.groups.map((a) => ({ id: a.id, label: a.id })))}
            </div>
            <div className={section}>
              <RailItem collapsed={c} href={wsHref(ws, "changes")} icon={FilePenLine} label="Changes" count={draft.items.length} countTitle="Unpublished changes" />
              {releases}
              <RailItem collapsed={c} href={wsHref(ws, "import")} icon={Import} label="Import" />
            </div>
          </>
        )}
      </>
    );
  };
  return (
    <>
      <RailFrame label={label} collapsed={collapsed}>
        {body(collapsed)}
      </RailFrame>
      <RailSheet label={label}>{body(false)}</RailSheet>
    </>
  );
}

function RailSheet({ label, children }: { label: string; children: ReactNode }) {
  const open = useRailOpen();
  const { pathname } = useLocation();
  useEffect(() => setRailOpen(false), [pathname]);
  return (
    <Sheet open={open} onOpenChange={setRailOpen}>
      <SheetContent
        aria-describedby={undefined}
        className="bg-card right-auto left-0 w-72 outline-none max-w-[85vw] overflow-auto border-r border-l-0 px-2.5 pt-3.5 pb-10 md:hidden data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left"
      >
        <SheetTitle className="px-2 pb-2 text-[15px] font-semibold">{label}</SheetTitle>
        <nav aria-label={label}>{children}</nav>
      </SheetContent>
    </Sheet>
  );
}
