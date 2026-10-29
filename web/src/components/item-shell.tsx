// An item page's header (crumb, badge, title, unpublished tag, tabs) and who uses it.
import { useState, type ReactNode } from "react";
import { KIND, type Kind, plural } from "@/lib/kinds";
import type { Use } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { Banner, KindBadge, PageTitle, Tag } from "./bits";
import { HrefLink } from "./href-link";

export function ItemHeader({ kind, id, title, tab }: { kind: Kind; id: string; title: ReactNode; tab: "" | "prompt" | "history" }) {
  const { href, changeOf } = useWs();
  const c = changeOf(kind, id);
  const tabs: [typeof tab, string][] =
    kind === "agent"
      ? [
          ["", "Builder"],
          ["prompt", "Instructions"],
          ["history", "History"],
        ]
      : [
          ["", "Setup"],
          ["history", "History"],
        ];
  return (
    <>
      {kind !== "settings" && (
        <div className="mb-0.5 text-sm">
          <HrefLink href={href(kind)} className="text-muted-foreground">
            {KIND[kind].many}
          </HrefLink>
        </div>
      )}
      <PageTitle aside={c && <Tag tone={c.change}>{c.change}, not published</Tag>}>
        {KIND[kind].badge && (
          <span className="mr-2 align-middle">
            <KindBadge kind={kind as never} />
          </span>
        )}
        {title}
      </PageTitle>
      <nav className="mt-3 mb-[22px] flex gap-0.5 border-b">
        {tabs.map(([t, label]) => (
          <HrefLink
            key={t}
            href={href(kind, id, t || undefined)}
            aria-current={tab === t ? "page" : undefined}
            className={cn(
              "-mb-px border-b-2 border-transparent px-3 py-2 text-muted-foreground hover:no-underline hover:text-foreground",
              tab === t && "border-primary text-foreground font-medium",
            )}
          >
            {label}
          </HrefLink>
        ))}
      </nav>
    </>
  );
}

export function NewHeader({ kind }: { kind: Kind }) {
  const { href } = useWs();
  return (
    <>
      <div className="mb-0.5 text-sm">
        <HrefLink href={href(kind)} className="text-muted-foreground">
          {KIND[kind].many}
        </HrefLink>
      </div>
      <h1 className="mb-5 text-2xl font-semibold">New {KIND[kind].one.toLowerCase()}</h1>
    </>
  );
}

export function UsedBy({ kind, uses }: { kind: Kind; uses: Use[] }) {
  const { href } = useWs();
  const agents = [...new Set(uses.map((u) => u.agent))];
  const [expanded, setExpanded] = useState(false);
  const visibleAgents = expanded ? agents : agents.slice(0, 10);
  return (
    <div className="-mt-2 mb-[18px]">
      <b>Used by: </b>
      {agents.length ? (
        <>
          {plural(agents.length, "agent", "agents")} —{" "}
          {visibleAgents.map((a, i) => {
            const via = uses.filter((u) => u.agent === a && u.via).map((u) => u.via);
            return (
              <span key={a}>
                {i ? ", " : ""}
                <HrefLink href={href("agent", a)}>{a}</HrefLink>
                {via.length > 0 && <span className="text-muted-foreground text-sm"> (through {via.join(", ")})</span>}
              </span>
            );
          })}
          {agents.length > 10 && (
            <button
              type="button"
              className="ml-2 text-sm underline underline-offset-2 hover:text-primary"
              aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? "Show fewer" : `See ${agents.length - 10} more`}
            </button>
          )}
        </>
      ) : (
        <span className="text-muted-foreground">{kind === "group" ? "no agent yet. Add it to an agent's blocks." : "no agent yet. Add it to an agent's blocks or to a group."}</span>
      )}
    </div>
  );
}

export function Missing({ kind, id }: { kind: Kind; id: string }) {
  const { href } = useWs();
  return (
    <>
      <Banner tone="err">
        There's no {KIND[kind].one.toLowerCase()} called {id}.
      </Banner>
      <HrefLink href={href(kind)}>All {KIND[kind].many.toLowerCase()}</HrefLink>
    </>
  );
}

export function Page({ children }: { wide?: boolean; children: ReactNode }) {
  // Every page fills the content column. `wide` is accepted for older callers and changes nothing.
  return <div className="w-full min-w-0">{children}</div>;
}
