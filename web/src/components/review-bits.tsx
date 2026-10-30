// Small pieces every page that shows patterns shares: verdict and review-state chips, suggestion status tags, the
// "suggestion being written" line, the "what should the fix do?" ask before one is written, and one lookup of every pattern's verdict and state from GET …/patterns.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, PencilLine, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { type PatternOut, type RuleRef, q } from "@/lib/api";
import { ruleName } from "@/lib/insights";
import { when } from "@/lib/kinds";
import { REVIEW_STATE, type ReviewState, type SuggestionSummary, VERDICT, type VerdictOut, review, rq, writing } from "@/lib/review";
import { cn } from "@/lib/utils";
import { wsHref, useWs } from "@/lib/workspace";
import { ErrorBanner, Note, RuleText, Tag } from "./bits";
import { HrefLink } from "./href-link";
import { Hint, TipBody } from "./insights/kit";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";

export function ago(t: string | null | undefined): string {
  if (!t) return "—";
  const s = (Date.now() - Date.parse(t)) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  return new Date(t).toLocaleDateString(undefined, { dateStyle: "medium" });
}

/** A rule pattern's secondary label ("Never rule 2"), next to its name; the rule as it reads now on hover. */
export function RuleTag({ rule, className }: { rule: RuleRef | null | undefined; className?: string }) {
  if (!rule?.kind) return null;
  const label = ruleName(rule.kind, rule.index);
  return (
    <Hint focusable={false} tip={<TipBody head={label}>{rule.text ? <RuleText text={rule.text} /> : "The rule's text isn't known yet."}</TipBody>}>
      <span className={cn("text-muted-foreground rounded border px-2 py-px text-xs font-normal whitespace-nowrap", className)}>{label}</span>
    </Hint>
  );
}

/** Why a closed pattern is back in review: its rule was reworded after an acceptable or checker-wrong verdict. */
export function RewordedNote({ reason, className }: { reason: string | null | undefined; className?: string }) {
  if (reason !== "reworded") return null;
  return (
    <div className={cn("bg-warn-soft text-warn flex items-start gap-2 rounded-md px-2 py-2 text-sm", className)} role="note">
      <PencilLine className="mt-0.5 size-3.5 shrink-0" />
      <span>
        <span className="font-medium">The rule was reworded after this verdict. Check it still applies.</span>{" "}
        The verdict judged the old wording, so findings on the new one count again until you give the verdict again, or another.
      </span>
    </div>
  );
}

export function VerdictChip({ v, className }: { v: VerdictOut | null | undefined; className?: string }) {
  if (!v)
    return (
      <Hint focusable={false} tip={<TipBody head="No verdict">Nobody has said yet whether this is a real problem.</TipBody>}>
        <span className={cn("text-muted-foreground rounded-full border border-dashed px-2 py-px text-xs whitespace-nowrap", className)}>No verdict</span>
      </Hint>
    );
  const m = VERDICT[v.verdict];
  return (
    <Hint
      focusable={false}
      tip={
        <TipBody head={m.label}>
          {m.help}
          {v.inherited ? " Given on the rule pattern; this pattern has none of its own." : ""}
          {v.carried_from ? " Carried over from a pattern a recluster retired." : ""}
          {v.note && <span className="mt-1 block break-words whitespace-pre-line">“{v.note}”</span>}
          <span className="mt-1 block">
            {v.reviewer ? `${v.reviewer}, ` : ""}
            {when(v.created_at)}
          </span>
        </TipBody>
      }
    >
      <span className={cn("inline-block rounded-full px-2 py-px text-xs font-medium whitespace-nowrap", m.tone, className)}>
        {m.label}
        {v.inherited && <span className="font-normal opacity-75"> · from rule</span>}
      </span>
    </Hint>
  );
}

/** Where a pattern is between a finding and a fix, with a one-sentence hover (and its note, when it has one). */
export function ReviewStateChip({ state, note, className }: { state: ReviewState | null | undefined; note?: string | null; className?: string }) {
  if (!state) return null;
  const m = REVIEW_STATE[state];
  return (
    <Hint
      focusable={false}
      tip={
        <TipBody head={m.label}>
          {m.help}
          {note && note !== "No verdict yet." ? ` ${note}` : ""}
        </TipBody>
      }
    >
      <span className={cn("inline-block rounded-full px-2 py-px text-xs font-medium whitespace-nowrap", m.tone, className)}>{m.label}</span>
    </Hint>
  );
}

export const SUGGESTION_STATUS: Record<SuggestionSummary["status"], { label: string; tone: string; help: string }> = {
  generating: { label: "Being written", tone: "bg-muted text-muted-foreground", help: "The fix is being written. It takes a minute or so." },
  proposed: { label: "Proposed", tone: "bg-warn-soft text-warn", help: "Written and waiting for someone to apply or dismiss it." },
  pr_open: { label: "PR open", tone: "bg-brand-soft text-primary", help: "Sent as a pull request. Waiting for it to be merged." },
  merged: { label: "PR merged", tone: "bg-brand-soft text-primary", help: "The pull request was merged. It goes live with the next release." },
  applied: { label: "Applied", tone: "bg-brand-soft text-primary", help: "Saved as a draft. It goes live with the next publish." },
  dismissed: { label: "Dismissed", tone: "bg-muted text-muted-foreground", help: "Turned down, or its pull request was closed." },
  failed: { label: "Failed", tone: "bg-err-soft text-err", help: "The fix couldn't be written. Open it for the reason." },
};

export function SuggestionStatusTag({ status, big }: { status: SuggestionSummary["status"]; big?: boolean }) {
  const s = SUGGESTION_STATUS[status] ?? { label: status, tone: "", help: "" };
  return (
    <Hint focusable={false} tip={<TipBody head={s.label}>{s.help}</TipBody>}>
      <Tag big={big} className={cn("border-transparent", s.tone)}>
        {s.label}
      </Tag>
    </Hint>
  );
}

/** On a proposed suggestion whose procedure changed since it was written, on the same lines (`freshness`). */
export function ConflictTag({ freshness, big }: { freshness: SuggestionSummary["freshness"]; big?: boolean }) {
  if (freshness !== "conflict") return null;
  return (
    <Hint focusable={false} tip={<TipBody head="Conflicts">The procedure changed since this was written, in the same place. It can't be applied. Rewrite it against the current text.</TipBody>}>
      <Tag big={big} className="bg-err-soft text-err border-transparent">
        Conflicts
      </Tag>
    </Hint>
  );
}

/** After asking for a fix: waits for it, then links to it. */
/** Before a fix is written: the button, and a link to add what it should do first (optional; empty: the verdict alone). */
export function FixAsk({ initial = "", busy, label = "Write the fix", onWrite, onCancel }: {
  initial?: string | null;
  busy?: boolean;
  label?: string;
  onWrite: (guidance: string | null) => void;
  onCancel?: () => void;
}) {
  const [text, setText] = useState(initial ?? "");
  const [adding, setAdding] = useState(Boolean(initial));
  const write = () => onWrite(adding ? text.trim() || null : null);
  return (
    <div className="grid gap-2">
      {adding && (
        <Textarea
          autoFocus
          rows={2}
          aria-label="What the fix should do"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => (e.metaKey || e.ctrlKey) && e.key === "Enter" && write()}
          placeholder="What the fix should do, e.g. make it a step, not guidance"
        />
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Button size="sm" disabled={busy} onClick={write}>
          {busy ? <Loader2 className="animate-spin" /> : <Sparkles />} {label}
        </Button>
        {!adding && (
          <button type="button" className="text-muted-foreground hover:text-foreground text-sm underline underline-offset-2" disabled={busy} onClick={() => setAdding(true)}>
            Add something before it writes
          </button>
        )}
        {onCancel && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </div>
  );
}

/** Ask what the fix should do, then write it and follow it until it's ready. */
export function AskThenWrite({ patternId, initial, onCancel }: { patternId: string; initial?: string | null; onCancel?: () => void }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [asked, setAsked] = useState<SuggestionSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (asked) return <SuggestionState patternId={patternId} initial={asked} />;
  const write = async (guidance: string | null) => {
    setBusy(true);
    setError(null);
    try {
      setAsked(await review.suggest(ws, patternId, guidance));
      void qc.invalidateQueries({ queryKey: ["ws", ws, "pattern", patternId] });
      void qc.invalidateQueries({ queryKey: ["ws", ws, "suggestions"] });
      void qc.invalidateQueries({ queryKey: ["ws", ws, "review"] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid gap-2">
      {error != null && <ErrorBanner error={error} />}
      <FixAsk initial={initial} busy={busy} onWrite={(g) => void write(g)} onCancel={onCancel} />
    </div>
  );
}

export function SuggestionState({ patternId, initial }: { patternId: string; initial: SuggestionSummary | null | undefined }) {
  const { ws } = useWs();
  const [waiting, setWaiting] = useState(true);
  const res = useQuery({ ...rq.pattern(ws, patternId), refetchInterval: waiting ? 2500 : false });
  const latest = res.data?.suggestions?.[0] ?? initial ?? null;
  const done = !!latest && !writing(latest);
  useEffect(() => {
    if (done) setWaiting(false);
  }, [done]);
  useEffect(() => {
    const t = window.setTimeout(() => setWaiting(false), 120_000);
    return () => window.clearTimeout(t);
  }, []);
  if (latest?.status === "failed")
    return (
      <span className="text-err inline-flex items-center gap-2 text-sm">
        The fix couldn't be written. <HrefLink href={wsHref(ws, "suggestions", latest.suggestion_id)}>Details</HrefLink>
      </span>
    );
  if (done && latest)
    return (
      <HrefLink href={wsHref(ws, "suggestions", latest.suggestion_id)} className="inline-flex items-center gap-2 text-sm font-semibold">
        <Sparkles className="size-3.5" /> Fix ready: review it
      </HrefLink>
    );
  if (!waiting) return <Note>The fix is taking a while. It will show on the Suggestions page when it's written.</Note>;
  return (
    <span className="text-muted-foreground inline-flex items-center gap-2 text-sm" role="status">
      <Loader2 className="size-3.5 animate-spin" /> Writing the fix…
    </span>
  );
}

/** Every pattern (rule patterns and the patterns they split into) by id, with its verdict and review state, from one GET …/patterns. */
export function usePatternIndex(ws: string) {
  const res = useQuery({ ...q.patterns(ws), retry: false, staleTime: 30_000 });
  const index = useMemo(() => {
    const m = new Map<string, PatternOut>();
    for (const p of res.data?.items ?? []) {
      m.set(p.pattern_id, p);
      for (const s of p.sub_patterns) m.set(s.pattern_id, s);
    }
    return m;
  }, [res.data]);
  return { index, loading: res.isPending, clusteredAt: res.data?.clustered_at ?? null };
}
