// How sopops builds an agent's instructions, as a picture: blocks written once, agents that list them in order, and the full instructions
// each agent gets. The import page shows it with an example before anything is imported, and the review's first step
// draws the same picture with what the import found.
import { ArrowDown, ArrowRight } from "lucide-react";
import type { ReactNode } from "react";
import type { BlockKind } from "@/lib/kinds";
import { usePlatforms } from "@/lib/platforms";
import { cn } from "@/lib/utils";
import { PlatformLabel } from "../brand-icon";

export type DiagramRef = { id: string; kind: BlockKind };
export type DiagramAgent = { id: string; platform?: string; context?: string; blocks: DiagramRef[] };
export type DiagramPrompt = { agent: string; lines: string[] };
export type DiagramBlocks = { instructions: string[]; procedures: string[]; groups: { id: string; members: string[] }[] };

const CHIP: Record<BlockKind, string> = {
  instruction: "text-k-instruction bg-k-instruction/10 border-k-instruction/35",
  procedure: "text-k-procedure bg-k-procedure/10 border-k-procedure/35",
  group: "text-k-group bg-k-group/12 border-k-group/40",
};
const DOT: Record<BlockKind, string> = { instruction: "bg-k-instruction", procedure: "bg-k-procedure", group: "bg-k-group" };

function Chip({ kind, children, aside }: { kind: BlockKind; children: ReactNode; aside?: ReactNode }) {
  return (
    <div className={cn("flex min-w-0 items-baseline gap-2 rounded-md border px-2.5 py-1 font-mono text-[12.5px]", CHIP[kind])}>
      <span className="min-w-0 truncate">{children}</span>
      {aside && <span className="ml-auto shrink-0 font-sans text-[11px] opacity-80">{aside}</span>}
    </div>
  );
}

function Column({ title, text, children }: { title: string; text: ReactNode; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-muted-foreground text-[11px] font-semibold tracking-wide uppercase">{title}</div>
      <p className="text-muted-foreground mb-2.5 text-[13px] leading-snug">{text}</p>
      {children}
    </div>
  );
}

function Arrow() {
  return (
    <div className="text-muted-foreground flex justify-center @2xl:items-center" aria-hidden>
      <ArrowDown className="size-4 @2xl:hidden" />
      <ArrowRight className="hidden size-4 @2xl:block" />
    </div>
  );
}

function KindSection({ kind, title, help, count, children }: { kind: BlockKind; title: string; help?: ReactNode; count?: number; children: ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <div className="flex items-center gap-1.5 text-[13px] font-semibold">
        <span className={cn("size-2 rounded-[3px]", DOT[kind])} />
        {title}
        {count !== undefined && <span className="text-muted-foreground font-normal">({count})</span>}
      </div>
      {help && <p className="text-muted-foreground -mt-1 text-[12.5px] leading-snug">{help}</p>}
      {children}
    </div>
  );
}

const MAX_BLOCKS = 6;

function More({ n, what }: { n: number; what: string }) {
  return n > 0 ? <div className="text-muted-foreground pl-1 text-[12px]">and {n} more {what}</div> : null;
}

/**
 * The three columns. `help` adds what each kind of block is for (the explainer); `onAgent` makes agent names buttons;
 * `more` sits under the agents (a "Show all" for a long import); `footer` runs under all three.
 */
export function BlocksDiagram({
  blocks,
  agents,
  prompts,
  help,
  onAgent,
  more,
  footer,
}: {
  blocks: DiagramBlocks;
  agents: DiagramAgent[];
  prompts: DiagramPrompt[];
  help?: boolean;
  onAgent?: (id: string) => void;
  more?: ReactNode;
  footer?: ReactNode;
}) {
  const plats = usePlatforms();
  const count = (n: number) => (help ? undefined : n);
  return (
    <div className="@container">
      <div className="grid gap-3 @2xl:grid-cols-[minmax(0,1fr)_16px_minmax(0,1.25fr)_16px_minmax(0,1fr)] @2xl:gap-x-3">
        <Column title="Blocks" text="Written once.">
          <div className="bg-card grid gap-3 rounded-lg border px-3 py-3">
            {(help || blocks.instructions.length > 0) && (
              <KindSection kind="instruction" title="Shared instructions" count={count(blocks.instructions.length)} help={help && "Text many agents use, like how to greet or the brand voice."}>
                {blocks.instructions.slice(0, MAX_BLOCKS).map((b) => (
                  <Chip key={b} kind="instruction">
                    {b}
                  </Chip>
                ))}
                <More n={blocks.instructions.length - MAX_BLOCKS} what="shared instructions" />
              </KindSection>
            )}
            {(help || blocks.procedures.length > 0) && (
              <KindSection
                kind="procedure"
                title="Procedures"
                count={count(blocks.procedures.length)}
                help={help && "Step-by-step tasks the agent must follow, like taking an order. These are what each call gets checked against."}
              >
                {blocks.procedures.slice(0, MAX_BLOCKS).map((b) => (
                  <Chip key={b} kind="procedure">
                    {b}
                  </Chip>
                ))}
                <More n={blocks.procedures.length - MAX_BLOCKS} what="procedures" />
              </KindSection>
            )}
            {(help || blocks.groups.length > 0) && (
              <KindSection kind="group" title="Groups" count={count(blocks.groups.length)} help={help && "Blocks that always go together, so you add them as one."}>
                {blocks.groups.slice(0, MAX_BLOCKS).map((g) => (
                  <div key={g.id} className={cn("rounded-md border px-2.5 py-1", CHIP.group)}>
                    <div className="truncate font-mono text-[12.5px]">{g.id}</div>
                    <div className="text-[12px] opacity-80">{g.members.join(", then ")}</div>
                  </div>
                ))}
                <More n={blocks.groups.length - MAX_BLOCKS} what="groups" />
              </KindSection>
            )}
          </div>
        </Column>
        <Arrow />
        <Column title="Agents" text="Each agent lists the blocks it uses, in order, after a few lines about itself.">
          <div className="bg-card grid gap-4 rounded-lg border px-3 py-3">
            {agents.map((a) => (
              <div key={a.id} className="grid gap-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  {onAgent ? (
                    <button type="button" className="text-primary cursor-pointer font-mono text-[13px] font-semibold hover:underline" onClick={() => onAgent(a.id)}>
                      {a.id}
                    </button>
                  ) : (
                    <span className="font-mono text-[13px] font-semibold">{a.id}</span>
                  )}
                  <span className="flex-1" />
                  {a.platform && <PlatformLabel platform={a.platform} size={12} className="text-muted-foreground text-[12px]" />}
                </div>
                {a.context && <div className="bg-muted line-clamp-2 rounded-md px-2.5 py-1 text-[12.5px] leading-snug">{a.context}</div>}
                <ol className="grid gap-1">
                  {a.blocks.map((b, i) => (
                    <li key={`${b.id}:${i}`} className="grid grid-cols-[14px_minmax(0,1fr)] items-center gap-1.5">
                      <span className="text-muted-foreground text-right text-[11px] tabular-nums">{i + 1}</span>
                      <Chip kind={b.kind} aside={b.kind === "group" ? "group" : undefined}>
                        {b.id}
                      </Chip>
                    </li>
                  ))}
                </ol>
              </div>
            ))}
            {more}
          </div>
        </Column>
        <Arrow />
        <Column title="Full instructions" text="sopops joins the blocks into one set of instructions per agent, and your platform gets them.">
          <div className="grid gap-2.5">
            {prompts.map((p) => (
              <div key={p.agent} className="bg-muted min-w-0 rounded-lg px-3 py-2.5 font-mono text-[12px] leading-relaxed">
                <div className="text-primary mb-1 truncate font-semibold">{p.agent}</div>
                {p.lines.map((l, i) => (
                  <div key={i} className={cn("truncate", l.startsWith("#") ? "text-k-procedure" : "text-muted-foreground")}>
                    {l}
                  </div>
                ))}
              </div>
            ))}
            {help && (
              <div className="flex flex-wrap gap-1.5">
                {plats.shown.map((p) => (
                    <span key={p} className="bg-card rounded-full border px-2 py-0.5 text-[12px]">
                      <PlatformLabel platform={p} size={12} />
                    </span>
                  ))}
              </div>
            )}
          </div>
        </Column>
      </div>
      {footer && <div className="mt-3 text-[13px]">{footer}</div>}
    </div>
  );
}

// --- the example, before importing ------------------------------------------------------------------

const EXAMPLE_BLOCKS: DiagramBlocks = {
  instructions: ["restaurant-host", "brand-voice", "closing"],
  procedures: ["allergen-check", "delivery-handling", "reservations"],
  groups: [{ id: "delivery-orders", members: ["allergen-check", "delivery-handling"] }],
};

const EXAMPLE_AGENTS: DiagramAgent[] = [
  {
    id: "tonys-pizza",
    context: "Wood-fired pizza shop in Brooklyn. Pickup only after 10pm.",
    blocks: [
      { id: "restaurant-host", kind: "instruction" },
      { id: "brand-voice", kind: "instruction" },
      { id: "delivery-orders", kind: "group" },
      { id: "closing", kind: "instruction" },
    ],
  },
  {
    id: "sakura-sushi",
    context: "Omakase counter in Manhattan. No delivery.",
    blocks: [
      { id: "restaurant-host", kind: "instruction" },
      { id: "brand-voice", kind: "instruction" },
      { id: "allergen-check", kind: "procedure" },
      { id: "reservations", kind: "procedure" },
      { id: "closing", kind: "instruction" },
    ],
  },
];

const EXAMPLE_PROMPTS: DiagramPrompt[] = [
  {
    agent: "tonys-pizza",
    lines: ["Tony's is a wood-fired pizza shop…", "You're the phone host at Tony's…", "Speak warmly and briefly…", "## Procedures", "### Allergen check", "### Delivery handling", "Before hanging up, repeat…"],
  },
  {
    agent: "sakura-sushi",
    lines: ["Omakase counter in Manhattan…", "You're the phone host at Sakura…", "## Procedures", "### Allergen check", "### Reservations", "Before hanging up, repeat…"],
  },
];

/** "How sopops builds an agent's instructions" with example restaurants, for the setup step before importing. */
export function BlocksExample() {
  const plats = usePlatforms();
  return (
    <BlocksDiagram
      help
      blocks={EXAMPLE_BLOCKS}
      agents={EXAMPLE_AGENTS.map((a) => ({ ...a, platform: plats.first }))}
      prompts={EXAMPLE_PROMPTS}
      footer="Edit a block once, and every agent that uses it changes."
    />
  );
}
