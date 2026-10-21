// Small shared pieces: kind badges, status tags, +/− counts, banners, issue and lint lists, two-click buttons.
import { type ReactNode, useEffect, useRef, useState } from "react";
import type { IssueOut, LintFinding } from "@/lib/api";
import { ApiError } from "@/lib/api";
import { type BlockKind, KIND, type Kind, plural } from "@/lib/kinds";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { Button } from "./ui/button";
import { HrefLink } from "./href-link";

const BADGE: Record<BlockKind, string> = {
  instruction: "text-k-instruction bg-k-instruction/13",
  procedure: "text-k-procedure bg-k-procedure/13",
  group: "text-k-group bg-k-group/15",
};

export function KindBadge({ kind }: { kind: BlockKind | null }) {
  return (
    <span
      className={cn(
        "inline-block shrink-0 rounded-[5px] px-1.5 py-px text-xs font-medium capitalize whitespace-nowrap",
        kind ? BADGE[kind] : "text-err bg-err-soft",
      )}
    >
      {kind ? KIND[kind].badge : "missing"}
    </span>
  );
}

const TAG: Record<string, string> = {
  changed: "bg-warn-soft text-warn border-transparent",
  added: "bg-warn-soft text-warn border-transparent",
  edited: "bg-warn-soft text-warn border-transparent",
  removed: "bg-err-soft text-err border-transparent",
  deleted: "bg-err-soft text-err border-transparent",
  live: "bg-brand-soft text-primary border-transparent",
};

export function Tag({ tone, big, children, className }: { tone?: string; big?: boolean; children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-block rounded-full border px-2 py-px text-xs whitespace-nowrap bg-muted text-muted-foreground",
        tone && TAG[tone],
        big && "px-2.5 py-0.5 text-[13px] font-semibold",
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Rule text, with each `{{placeholder}}` still in it (a rule shared by agents that each set their own value) as a chip. */
export function RuleText({ text }: { text: string | null | undefined }) {
  if (!text) return null;
  const parts = text.split(/\{\{\s*([A-Za-z0-9_]+)\s*\}\}/);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 ? (
          <span key={i} className="bg-muted text-muted-foreground rounded border px-1 font-mono text-[0.9em]" title={`Each agent's own ${part}`}>
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </>
  );
}

export function Counts({ add, del }: { add: number; del: number }) {
  return (
    <span className="font-mono text-[13px] font-semibold whitespace-nowrap" aria-label={`${add} lines added, ${del} removed`}>
      <span className="text-ok">+{add}</span> <span className="text-err">−{del}</span>
    </span>
  );
}

const BANNER = {
  ok: "bg-brand-soft border-transparent",
  err: "bg-err-soft border-transparent",
  warn: "bg-warn-soft border-transparent",
  info: "bg-muted",
};

export function Banner({ tone = "info", children, className }: { tone?: keyof typeof BANNER; children: ReactNode; className?: string }) {
  return (
    <div role={tone === "err" ? "alert" : "status"} className={cn("mb-4 rounded-lg border px-3.5 py-2.5 [&_ul]:mt-1.5 [&_ul]:list-disc [&_ul]:pl-5 [&_a]:font-semibold", BANNER[tone], className)}>
      {children}
    </div>
  );
}

/** sopc's issues; an issue about another item links to it. */
export function IssueList({ issues, here }: { issues: IssueOut[]; here?: { kind: Kind; id: string } }) {
  const { href, model } = useWs();
  return (
    <ul>
      {issues.map((i, n) => {
        const kind = i.kind as Kind | null | undefined;
        const elsewhere = kind && KIND[kind] && !(here && kind === here.kind && i.id === here.id);
        return (
          <li key={n}>
            {elsewhere && (
              <>
                <HrefLink href={href(kind, i.id ?? undefined)} className="text-inherit">
                  {kind === "settings" ? "Settings" : `${KIND[kind].one} ${model.itemName(kind, i.id ?? "")}`}
                </HrefLink>
                {": "}
              </>
            )}
            {i.text || i.message}
          </li>
        );
      })}
    </ul>
  );
}

export function ErrorBanner({ error, lead = "This change can't be saved yet:" }: { error: unknown; lead?: string }) {
  const issues = error instanceof ApiError ? error.issues : [];
  if (!issues.length) return <Banner tone="err">{error instanceof Error ? error.message : String(error)}</Banner>;
  return (
    <Banner tone="err">
      {lead}
      <IssueList issues={issues} />
    </Banner>
  );
}

// `sopc lint` names blocks as "instruction `brand-voice`", "SOP `allergen-check`" or "agent `tonys-pizza`".
const LINT_KINDS: Record<string, Kind> = { agent: "agent", instruction: "instruction", SOP: "procedure" };
function LintSource({ label }: { label: string }) {
  const { href, model } = useWs();
  const m = /^(agent|instruction|SOP) `([^`]+)`$/.exec(label);
  const kind = m && LINT_KINDS[m[1]];
  if (!m || !kind) return <>{label}</>;
  return (
    <HrefLink href={href(kind, m[2])} className="text-inherit">
      {KIND[kind].one} {kind === "agent" ? m[2] : model.itemName(kind, m[2])}
    </HrefLink>
  );
}

export function LintList({ findings }: { findings: LintFinding[] }) {
  return (
    <ul>
      {findings.map((f, i) => (
        <li key={i}>
          {f.message} ({plural(f.agents.length, "agent", "agents")}: {f.agents.join(", ")})
          <ul>
            {f.sources.map((s, j) => (
              <li key={j}>
                <LintSource label={s.block} />: {f.code === "unused_variable" ? `{{${s.text}}}` : `"${s.text}"`}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

/** Two clicks instead of a confirm dialog: the first arms it for a few seconds. */
export function ConfirmButton({
  children,
  confirm,
  onConfirm,
  ...props
}: Omit<React.ComponentProps<typeof Button>, "onClick"> & { confirm: string; onConfirm: () => void }) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return (
    <Button
      {...props}
      onClick={() => {
        if (armed) {
          setArmed(false);
          window.clearTimeout(timer.current);
          onConfirm();
          return;
        }
        setArmed(true);
        timer.current = window.setTimeout(() => setArmed(false), 4000);
      }}
    >
      {armed ? confirm : children}
    </Button>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  small,
}: {
  value: T;
  options: [T, string][];
  onChange: (v: T) => void;
  label: string;
  small?: boolean;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex overflow-hidden rounded-md border border-input bg-card">
      {options.map(([v, l]) => (
        <button
          key={v}
          type="button"
          aria-pressed={value === v}
          onClick={() => onChange(v)}
          className={cn(
            "border-r border-border last:border-r-0 cursor-pointer",
            small ? "px-2.5 py-0.5 text-[13px]" : "px-3 py-1.5",
            value === v ? "bg-brand-soft font-semibold" : "hover:bg-muted",
          )}
        >
          {l}
        </button>
      ))}
    </div>
  );
}

export function Panel({ title, children, className }: { title?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("mb-4 rounded-xl border bg-card px-4 py-3.5", className)}>
      {title && <h2 className="mb-2 text-base font-semibold">{title}</h2>}
      {children}
    </section>
  );
}

export function PageTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-1 flex flex-wrap items-center gap-2.5">
      <h1 className="text-[22px] leading-tight font-semibold tracking-tight">{children}</h1>
      {aside}
    </div>
  );
}

export function Sub({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn("text-muted-foreground mt-1 mb-[18px] max-w-[75ch]", className)}>{children}</p>;
}

export function Note({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("text-muted-foreground text-[13px]", className)}>{children}</span>;
}
