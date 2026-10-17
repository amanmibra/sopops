// The frame: the top bar (workspace, author, unpublished changes, theme) and the workspace's side rail.
import { useQuery } from "@tanstack/react-query";
import { useLocation, useParams } from "@tanstack/react-router";
import { Monitor, Moon, Plus, Sun } from "lucide-react";
import { type ReactNode, useState } from "react";
import { q } from "@/lib/api";
import { KIND, type Kind, type ListKind, WORKSPACE_ID } from "@/lib/kinds";
import { prefs } from "@/lib/prefs";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Tag } from "./bits";
import { HrefLink, useGo } from "./href-link";
import { Logo } from "./logo";
import { PublishButton } from "./publish";
import { Button } from "./ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "./ui/dropdown-menu";
import { Input } from "./ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";

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
  const go = useGo();
  const workspaces = useQuery(q.workspaces());
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState(() => prefs.get("author") ?? "");
  const names = (workspaces.data ?? []).map((w) => w.workspace);
  if (ws && !names.includes(ws)) names.push(ws);
  return (
    <header className="bg-card sticky top-0 z-20 flex flex-wrap items-center gap-2.5 border-b px-5 py-2.5">
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
      ) : (
        <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={() => setAdding(true)}>
          <Plus /> New workspace
        </Button>
      )}
      <span className="flex-1" />
      <Input
        className="h-8 w-36"
        placeholder="Your name"
        aria-label="Your name, recorded with changes"
        value={name}
        onChange={(e) => {
          setName(e.target.value);
          prefs.set("author", e.target.value.trim());
        }}
      />
      {ws && <PublishButton ws={ws} />}
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

export function Rail() {
  const { ws, cfg, legacy, draft, href, changeOf } = useWs();
  const go = useGo();
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
    <nav className="bg-card border-b px-2.5 pt-3.5 pb-10 md:sticky md:top-[53px] md:h-[calc(100vh-53px)] md:overflow-auto md:border-r md:border-b-0" aria-label="Workspace contents">
      {legacy ? (
        <div className="mb-[18px]">{releases}</div>
      ) : (
        <>
          <div className="mb-[18px]">
            <RailLink href={wsHref(ws, "overview")}>Overview</RailLink>
            <RailLink href={wsHref(ws, "sessions")}>Sessions</RailLink>
          </div>
          {group("agent", cfg.agents.map((a) => ({ id: a.id, label: a.id })))}
          {group("instruction", cfg.instructions.map((a) => ({ id: a.id, label: a.id })))}
          {group("procedure", [...cfg.procedures].sort((a, b) => a.name.localeCompare(b.name)).map((p) => ({ id: p.id, label: p.name })))}
          {group("group", cfg.groups.map((a) => ({ id: a.id, label: a.id })))}
          <div className="mb-[18px]">
            <RailLink href={href("settings" as Kind)} change={changeOf("settings", "settings")?.change}>
              Settings
            </RailLink>
            {releases}
          </div>
        </>
      )}
    </nav>
  );
}
