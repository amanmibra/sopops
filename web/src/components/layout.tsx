// The frame: the top bar (team, author or user menu, unpublished changes, settings, theme) and the workspace's side
// rail, one list in the order of the loop: Home, then Review (mistakes, fixes, sessions), Results, Instructions, Publish.
import { useQuery } from "@tanstack/react-query";
import { useLocation, useParams } from "@tanstack/react-router";
import {
  CircleAlert,
  Wrench,
  Bot,
  Boxes,
  ChartLine,
  FileText,
  FilePenLine,
  GitCompare,
  ChevronDown,
  Plus,
  House,
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
  TestTube2,
  SlidersHorizontal,
} from "lucide-react";
import { type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { type AuthState, q } from "@/lib/api";
import { rq } from "@/lib/review";
import { KIND, type ListKind } from "@/lib/kinds";
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

/** Workspace-wide settings (tokens, GitHub repository, platforms, defaults), behind a gear. */
function SettingsLink({ ws }: { ws: string }) {
  const { pathname } = useLocation();
  const draft = useQuery(q.draft(ws));
  const changed = draft.data?.items.find((i) => i.kind === "settings");
  const here = routeIsSettings(pathname);
  return (
    <Button asChild variant="ghost" size="icon-sm" className={cn("relative", here && "bg-brand-soft hover:bg-brand-soft")}>
      <HrefLink href={settingsHref(ws)} aria-label="Settings" aria-current={here ? "page" : undefined} title="Settings: tokens, GitHub, platforms, defaults" className="text-foreground hover:no-underline">
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
  return (
    <Bar
      ws={ws}
      auth={auth}
      menu={<RailMenuButton />}
      end={
        <>
          <PublishButton ws={ws} />
          <SettingsLink ws={ws} />
        </>
      }
    />
  );
}

/**
 * The team's name (a team has one workspace, so there's nothing to pick). With sign-in off, an older install with
 * several workspaces keeps a picker, so none of them is out of reach.
 */
function Bar({ ws, auth, menu, end }: { ws?: string; auth: AuthState; menu?: ReactNode; end?: ReactNode }) {
  const go = useGo();
  const workspaces = useQuery({ ...q.workspaces(), enabled: !auth.required });
  const [name, setName] = useState(() => prefs.get("author") ?? "");
  const names = (workspaces.data ?? []).map((w) => w.workspace);
  if (ws && names.length && !names.includes(ws)) names.push(ws);
  return (
    <header className="bg-card sticky top-[var(--band-h)] z-20 flex flex-wrap items-center gap-x-1 gap-y-2 border-b px-4 py-3 sm:gap-2 md:px-6">
      {menu}
      <HrefLink href="/" className="text-foreground flex items-center gap-2 text-lg sm:mr-2 font-semibold hover:no-underline">
        <Logo />
        <span className={cn(ws && "max-sm:sr-only")}>sopops</span>
      </HrefLink>
      {auth.org && <span className="text-muted-foreground max-w-48 truncate text-sm" title="Your team">{auth.org.name}</span>}
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
    <span className="bg-warn-soft text-warn shrink-0 rounded-full px-2 text-sm font-medium tabular-nums" title={title} aria-label={`${n} ${title.toLowerCase()}`}>
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
        muted ? "text-muted-foreground text-sm" : "text-foreground",
        collapsed && "mx-auto size-9 justify-center p-0",
        active && "bg-brand-soft hover:bg-brand-soft",
      )}
    >
      <Icon className={cn("text-muted-foreground size-4 shrink-0", active && "text-foreground")} />
      {collapsed ? (
        count ? (
          <span className="bg-warn text-card absolute -top-1 -right-1 min-w-4 rounded-full px-1 text-center text-sm leading-4 font-semibold tabular-nums">{count}</span>
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
    <nav aria-label={label} className="bg-card sticky top-[calc(57px+var(--band-h))] hidden h-[calc(100vh-57px-var(--band-h))] flex-col border-r md:flex">
      <div className={cn("min-h-0 flex-1 overflow-auto pt-4 pb-4", collapsed ? "px-2" : "px-2")}>{children}</div>
      <div className="flex px-2 py-2">
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

/** Keys for the loop's main pages: g h Home, g m Mistakes, g f Fixes, g s Sessions. */
const JUMPS: Record<string, string> = { h: "", m: "patterns", f: "suggestions", s: "sessions" };
function useJumpKeys(ws: string) {
  const go = useGo();
  const armed = useRef(0);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return;
      if (e.key === "g") {
        armed.current = Date.now();
        return;
      }
      const to = JUMPS[e.key];
      if (to !== undefined && Date.now() - armed.current < 1500) {
        e.preventDefault();
        go(to ? wsHref(ws, to) : wsHref(ws));
      }
      armed.current = 0;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ws, go]);
}

const RAIL_SHOWN = 5;

/**
 * One of the team's lists under Instructions: its heading (a link to the list page) with a collapse chevron and + New,
 * then up to five items, with "Show all N" for the rest. The item for the page you're on always shows. Whether the
 * list is collapsed is remembered per workspace.
 */
function RailList({
  ws,
  kind,
  heading,
  count,
  onNew,
  items,
}: {
  ws: string;
  kind: ListKind;
  heading: ReactNode;
  count: number;
  onNew: () => void;
  items: { id: string; label: string; href: string; change?: string }[];
}) {
  const pref = `rail.${ws}.${kind}.collapsed`;
  const [closed, setClosed] = useState(() => prefs.get(pref) === "1");
  const [all, setAll] = useState(false);
  const here = decodeURIComponent(useLocation().pathname);
  const isHere = (h: string) => here === decodeURIComponent(h) || here.startsWith(decodeURIComponent(h) + "/");
  const shown = all ? items : items.filter((it, i) => i < RAIL_SHOWN || isHere(it.href));
  const listId = `rail-${kind}`;
  return (
    <div className="mb-2">
      <div className="flex items-center">
        <div className="min-w-0 flex-1">{heading}</div>
        {closed ? (
          <span className="text-muted-foreground px-2 tabular-nums" aria-label={`${count} in the list`}>
            {count}
          </span>
        ) : (
          <Button size="icon-xs" variant="ghost" className="text-muted-foreground" aria-label={`New ${KIND[kind].one.toLowerCase()}`} title={`New ${KIND[kind].one.toLowerCase()}`} onClick={onNew}>
            <Plus />
          </Button>
        )}
        <Button
          size="icon-xs"
          variant="ghost"
          className="text-muted-foreground"
          aria-expanded={!closed}
          aria-controls={listId}
          aria-label={`${closed ? "Show" : "Hide"} ${KIND[kind].many.toLowerCase()}`}
          onClick={() => {
            setClosed(!closed);
            prefs.set(pref, closed ? "0" : "1");
          }}
        >
          <ChevronDown className={cn("transition-transform", closed && "-rotate-90")} />
        </Button>
      </div>
      {!closed && (
        <div id={listId}>
          {shown.map((it) => (
            <RailLink key={it.id} href={it.href} change={it.change}>
              {it.label}
            </RailLink>
          ))}
          {!items.length && <div className="text-muted-foreground py-1 pr-2 pl-8 text-sm">None yet</div>}
          {items.length > RAIL_SHOWN && (
            <Button size="xs" variant="ghost" className="text-muted-foreground ml-6" onClick={() => setAll(!all)}>
              {all ? "Show fewer" : `Show all ${items.length}`}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

/** A group's name in the rail, in the label style; collapsed, a rule takes its place. */
function RailGroup({ label, collapsed, children }: { label?: string; collapsed: boolean; children: ReactNode }) {
  return (
    <div className={collapsed ? "mb-2 grid gap-1 border-t pt-2 first:border-t-0 first:pt-0" : "mb-4"}>
      {label && !collapsed && <h2 className="text-muted-foreground px-2 pb-2 text-sm">{label}</h2>}
      {children}
    </div>
  );
}

export function Rail() {
  const { ws, cfg, legacy, draft, href, changeOf } = useWs();
  const go = useGo();
  const { pathname } = useLocation();
  const needsReview = useNeedsReview(ws);
  const proposed = useQuery({ ...rq.suggestions(ws, "proposed"), retry: false, enabled: !legacy }).data?.items.length ?? null;
  const collapsed = useRailCollapsed();
  useJumpKeys(ws);
  const label = legacy ? "Team files" : ws;
  const home = decodeURIComponent(pathname).replace(/\/$/, "") === `/w/${ws}`;
  const agentsActive = new RegExp(`^/w/${ws}/(agents|overview|import)(/|$)`).test(decodeURIComponent(pathname));

  const body = (c: boolean) => {
    // One list of the team's items (agents, procedures): a row with its name and + New, then each item.
    const list = (kind: ListKind, name: string, items: { id: string; label: string }[], active?: boolean) => {
      const KindIcon = KIND_ICON[kind];
      if (c) return <RailItem key={kind} collapsed href={href(kind)} icon={KindIcon} label={name} active={active} />;
      return (
        <RailList
          key={kind}
          ws={ws}
          kind={kind}
          heading={<RailItem href={href(kind)} icon={KindIcon} label={name} active={active} />}
          count={items.length}
          onNew={() => go(href(kind, "new"))}
          items={items.map((it) => ({ ...it, href: href(kind, it.id), change: changeOf(kind, it.id)?.change }))}
        />
      );
    };
    const releases = (
      <RailItem collapsed={c} href={wsHref(ws, "releases")} icon={TagIcon} label="Releases" extra={draft.release ? <Tag tone="live">#{draft.release} live</Tag> : undefined} />
    );
    if (legacy) return <RailGroup collapsed={c}>{releases}</RailGroup>;
    return (
      <>
        {!c && <RailSetUp />}
        <RailGroup collapsed={c}>
          <RailItem collapsed={c} href={wsHref(ws)} icon={House} label="Home" active={home} />
        </RailGroup>
        <RailGroup label="Review" collapsed={c}>
          <RailItem collapsed={c} href={wsHref(ws, "patterns")} icon={CircleAlert} label="Mistakes" count={needsReview} countTitle="Mistakes to review: no verdict yet, or back after a fix" />
          <RailItem collapsed={c} href={wsHref(ws, "suggestions")} icon={Wrench} label="Fixes" count={proposed} countTitle="Fixes waiting for a decision" />
          <RailItem collapsed={c} href={wsHref(ws, "sessions")} icon={MessagesSquare} label="Sessions" />
          <RailItem collapsed={c} href={wsHref(ws, "fine-tune")} icon={SlidersHorizontal} label="Fine tune" />
        </RailGroup>
        <RailGroup label="Results" collapsed={c}>
          <RailItem collapsed={c} href={wsHref(ws, "results")} icon={ChartLine} label="Overview" />
          <RailItem collapsed={c} href={wsHref(ws, "results", "releases")} icon={GitCompare} label="Release compare" />
          <RailItem collapsed={c} href={wsHref(ws, "results", "versions")} icon={TestTube2} label="A/B tests" />
          <RailItem collapsed={c} href={wsHref(ws, "report")} icon={FileText} label="Review report" />
        </RailGroup>
        <RailGroup label="Instructions" collapsed={c}>
          {list("agent", "Agents", cfg.agents.map((a) => ({ id: a.id, label: a.id })), agentsActive)}
          {list("instruction", "Shared instructions", cfg.instructions.map((a) => ({ id: a.id, label: a.id })))}
          {list("procedure", "Procedures", [...cfg.procedures].sort((a, b) => a.name.localeCompare(b.name)).map((p) => ({ id: p.id, label: p.name })))}
          {list("group", "Groups", cfg.groups.map((a) => ({ id: a.id, label: a.id })))}
        </RailGroup>
        <RailGroup label="Publish" collapsed={c}>
          <RailItem collapsed={c} href={wsHref(ws, "changes")} icon={FilePenLine} label="Changes" count={draft.items.length} countTitle="Unpublished changes" />
          {releases}
        </RailGroup>
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
        className="bg-card right-auto left-0 w-72 outline-none max-w-[85vw] overflow-auto border-r border-l-0 px-2 pt-4 pb-10 md:hidden data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left"
      >
        <SheetTitle className="px-2 pb-2 text-lg font-semibold">{label}</SheetTitle>
        <nav aria-label={label}>{children}</nav>
      </SheetContent>
    </Sheet>
  );
}
