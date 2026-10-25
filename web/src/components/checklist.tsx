// A new team's home: one "Get set up" card of what's left, each ticked from what's really there, each linking to where
// it's done. Hidden once everything's done, or when someone hides it.
import { useQuery } from "@tanstack/react-query";
import { useLocation } from "@tanstack/react-router";
import { ArrowRight, Check, X } from "lucide-react";
import { useState } from "react";
import { call, sdk } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { platformName } from "@/lib/kinds";
import { listNames, usePlatforms } from "@/lib/platforms";
import { checklist, setupHref, setupProgress, STEP_TITLE } from "@/lib/setup";
import { cn } from "@/lib/utils";
import { settingsHref, useWs, wsHref } from "@/lib/workspace";
import { HrefLink } from "./href-link";
import { useCallsConnected, useFirstSession } from "./setup";
import { Button } from "./ui/button";

type Item = { key: string; title: string; text: string; href: string; done: boolean };

/** The items, ticked from real data; null while any of it is loading. */
function useItems(): Item[] | null {
  const { ws, cfg, releases } = useWs();
  const auth = useAuth().data;
  const plats = usePlatforms();
  const connected = useCallsConnected(ws);
  const session = useFirstSession(ws);
  const manager = auth?.org?.role === "owner" || auth?.org?.role === "admin";
  const members = useQuery({ queryKey: ["org", "members"], queryFn: () => call(sdk.listMembers()), enabled: Boolean(auth?.required && auth.org) });
  const link = useQuery({ queryKey: ["org", "join-link"], queryFn: () => call(sdk.getJoinLink()), enabled: Boolean(auth?.required && manager) });
  if (session === undefined) return null;
  const names = listNames(plats.shown.map(platformName));
  const items: Item[] = [
    { key: "agents", title: "Import your agents", text: "Bring in the instructions they run today.", href: wsHref(ws, "import"), done: cfg.agents.length > 0 },
    { key: "publish", title: "Publish your first release", text: "Agents get their instructions from it.", href: wsHref(ws, "changes"), done: releases.length > 0 },
    {
      key: "connect",
      title: `Connect calls from ${names}`,
      text: "So each call comes back to be checked.",
      href: `${settingsHref(ws, "platforms")}?from=qa`,
      done: connected,
    },
    { key: "session", title: "See your first session", text: "A call or chat, checked against your procedures.", href: wsHref(ws, "sessions"), done: session },
  ];
  if (auth?.required && auth.org)
    items.push({
      key: "team",
      title: "Bring in your team",
      text: "Share a join link from Members.",
      href: `${settingsHref(ws, "members")}?from=qa`,
      done: (members.data?.length ?? 1) > 1 || Boolean(link.data),
    });
  return items;
}

/** Whether a team's home shows the card: it's new (no session yet, or setup started in this browser), not all done, not hidden. */
export function useChecklist(): { show: boolean; items: Item[] | null } {
  const { ws } = useWs();
  const items = useItems();
  if (!items || checklist.hidden(ws)) return { show: false, items };
  const all = items.every((i) => i.done);
  const fresh = !items.find((i) => i.key === "session")?.done || setupProgress.started(ws);
  return { show: !all && fresh, items };
}

export function GetSetUp({ items, empty, onHide }: { items: Item[]; empty: boolean; onHide: () => void }) {
  const { ws, href } = useWs();
  const auth = useAuth().data;
  const done = items.filter((i) => i.done).length;
  const resume = setupProgress.get(ws);
  return (
    <div className="max-w-[640px] py-4">
      <h1 className="mb-1 text-2xl font-semibold">{auth?.org?.name ? `Welcome to ${auth.org.name}` : "Welcome"}</h1>
      <p className="text-muted-foreground mb-5">
        {done} of {items.length} done.
        {resume && (
          <>
            {" "}
            <HrefLink href={setupHref(ws, resume.step, resume.flow)}>Go back to setup: {STEP_TITLE[resume.step].toLowerCase()}</HrefLink>
          </>
        )}
      </p>
      <section className="bg-card rounded-xl border">
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <h2 className="flex-1 text-base font-semibold">Get set up</h2>
          <Button
            size="sm"
            variant="ghost"
            className="text-muted-foreground"
            onClick={() => {
              checklist.hide(ws);
              onHide();
            }}
          >
            Hide
          </Button>
        </div>
        <ol className="divide-y">
          {items.map((i, n) => (
            <li key={i.key}>
              <HrefLink href={i.href} className="text-foreground flex items-center gap-3 px-4 py-3 hover:bg-muted hover:no-underline">
                <span
                  className={cn(
                    "flex size-7 shrink-0 items-center justify-center rounded-full border text-sm font-semibold",
                    i.done ? "border-primary bg-primary text-primary-foreground" : "text-muted-foreground",
                  )}
                  aria-label={i.done ? "Done" : `Step ${n + 1}`}
                >
                  {i.done ? <Check className="size-4" /> : n + 1}
                </span>
                <span className="min-w-0 flex-1">
                  <span className={cn("block font-medium", i.done && "text-muted-foreground line-through decoration-1")}>{i.title}</span>
                  {!i.done && <span className="text-muted-foreground block text-sm">{i.text}</span>}
                </span>
                {!i.done && <ArrowRight className="text-muted-foreground size-4 shrink-0" />}
              </HrefLink>
            </li>
          ))}
        </ol>
      </section>
      <p className="text-muted-foreground mt-4 text-sm">
        {empty ? (
          <>
            Or start from nothing: a <HrefLink href={href("agent", "new")}>new agent</HrefLink>, <HrefLink href={href("instruction", "new")}>shared instruction</HrefLink> or{" "}
            <HrefLink href={href("procedure", "new")}>procedure</HrefLink>.
          </>
        ) : (
          <HrefLink href={wsHref(ws, "results")}>Go to Results</HrefLink>
        )}
      </p>
    </div>
  );
}

/** At the top of the side rail while the home's card would show: how far along, and a way back to it. Closes per team. */
export function RailSetUp() {
  const { ws } = useWs();
  const { pathname } = useLocation();
  const [, closed] = useState(0);
  const { show, items } = useChecklist();
  const home = pathname === wsHref(ws) || pathname === `${wsHref(ws)}/`;
  if (!show || !items || home || checklist.railClosed(ws)) return null;
  const done = items.filter((i) => i.done).length;
  return (
    <div className="bg-brand-soft hover:bg-brand-soft/80 relative mb-[18px] rounded-lg">
      <HrefLink href={wsHref(ws)} className="text-foreground block px-2 py-2 pr-8 hover:no-underline">
        <span className="block text-sm font-medium">Finish setting up</span>
        <span className="text-muted-foreground block text-sm tabular-nums">
          {done} of {items.length} done
        </span>
        <span className="bg-background mt-2 block h-1 overflow-hidden rounded-full" aria-hidden>
          <span className="bg-primary block h-full rounded-full" style={{ width: `${(done / items.length) * 100}%` }} />
        </span>
      </HrefLink>
      <Button
        size="icon-xs"
        variant="ghost"
        className="text-muted-foreground absolute top-1 right-1"
        aria-label="Close"
        title="Close. Your team's home still shows the steps."
        onClick={() => {
          checklist.closeRail(ws);
          closed((n) => n + 1);
        }}
      >
        <X />
      </Button>
    </div>
  );
}
