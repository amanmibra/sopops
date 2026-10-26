// Calls on one rule of a session, in a dialog: "Wrong?" on a finding (right or wrong, and what actually happened),
// "Wrong status" (any rule) and "Checker missed this" (a done step or an ok never rule). The row owns the dialog, so it
// doesn't depend on the row's buttons staying shown. Also the note a corrected rule shows: the status, who and when.
import { useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import type { RuleResultOut } from "@/lib/api";
import { ruleName } from "@/lib/insights";
import { when } from "@/lib/kinds";
import { KIND_STATUSES, MISSED_STATUSES, RULE_STATUS, type RuleCorrectionOut, type RuleStatus, isFinding, review, statusPhrase } from "@/lib/review";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { RuleText, Tag } from "./bits";
import { author } from "./form";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./ui/dialog";
import { Textarea } from "./ui/textarea";

/** One button per status, the picked one pressed. `keys` numbers them (1, 2, ...). */
export function StatusButtons({ statuses, picked, onPick, keys, disabled }: { statuses: RuleStatus[]; picked?: RuleStatus | null; onPick: (s: RuleStatus) => void; keys?: boolean; disabled?: boolean }) {
  return (
    <div className="flex flex-wrap gap-2">
      {statuses.map((s, i) => (
        <Button key={s} size="sm" variant="outline" disabled={disabled} aria-pressed={picked === s} className={cn(picked === s && "bg-brand-soft border-primary/40")} onClick={() => onPick(s)}>
          {keys && <kbd className="text-muted-foreground rounded border px-1 font-mono text-sm leading-4">{i + 1}</kbd>}
          <Tag className={RULE_STATUS[s].tone}>{RULE_STATUS[s].label}</Tag>
        </Button>
      ))}
    </div>
  );
}

export type CallMode = "override" | "status" | "missed";

const COPY: Record<CallMode, { title: string; help: string }> = {
  override: { title: "Is this finding right?", help: "Incorrect findings are hidden from rates and used to recalibrate the checker." },
  status: { title: "What actually happened?", help: "Pick the status this rule should have. It's kept with the session and used to recalibrate the checker." },
  missed: {
    title: "What did the checker miss?",
    help: "The agent got this wrong and the checker passed it. It's shown here and used to recalibrate; the session's result and Insights keep what the checker found.",
  },
};

const typingIn = (t: EventTarget | null) => t instanceof HTMLElement && (t.tagName === "TEXTAREA" || t.tagName === "INPUT");

/** The dialog for one call on a rule. `mode` null: closed. `onClosed` returns focus to the row. */
export function RuleCallDialog({
  mode,
  onOpenChange,
  onClosed,
  sessionId,
  procedureId,
  rule,
}: {
  mode: CallMode | null;
  onOpenChange: (open: boolean) => void;
  onClosed?: () => void;
  sessionId: string;
  procedureId: string;
  rule: RuleResultOut;
}) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [correct, setCorrect] = useState<boolean | null>(null);
  const [picked, setPicked] = useState<RuleStatus | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (mode) {
      setCorrect(mode === "override" ? (rule.override ?? null) : null);
      setPicked(null);
      setNote("");
    }
  }, [mode, rule.override]);
  if (!mode) return <Dialog open={false} onOpenChange={onOpenChange} />;
  // a right finding happened as some finding status (out of order rather than skipped); a wrong one as something else
  const statuses =
    mode === "override" ? (correct == null ? [] : KIND_STATUSES[rule.kind].filter((s) => isFinding(s) === correct)) : mode === "missed" ? MISSED_STATUSES[rule.kind] : KIND_STATUSES[rule.kind].filter((s) => s !== rule.status);
  const ready = mode === "override" ? correct != null : picked != null;
  const copy = COPY[mode];

  const save = async () => {
    if (!ready || busy) return;
    setBusy(true);
    try {
      if (mode === "override") {
        if (!rule.finding_id) return;
        await review.override(ws, rule.finding_id, { correct: !!correct, status: picked, note: note.trim() || null, reviewer: author() });
        const what = picked ? ` What happened: ${statusPhrase(picked)}.` : "";
        toast.success(correct ? `Marked correct.${what}` : `Marked incorrect. It's hidden from rates and used to recalibrate.${what}`);
      } else if (picked) {
        const r = await review.label(ws, sessionId, procedureId, rule.rule_id, { status: picked, note: note.trim() || null, reviewer: author(), source: "correction" });
        toast.success(r.missed ? `Saved: the checker missed this (${statusPhrase(picked)}).` : r.suppressed ? `Corrected to ${statusPhrase(picked)}. The finding is hidden from rates.` : `Corrected to ${statusPhrase(picked)}.`);
      }
      onOpenChange(false);
      void qc.invalidateQueries({ queryKey: ["ws", ws, "session"] });
      void qc.invalidateQueries({ queryKey: ["ws", ws, "insights"] });
      void qc.invalidateQueries({ queryKey: ["ws", ws, "spot-checks"] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.metaKey || e.ctrlKey || e.altKey) {
      if (e.key === "Enter") (e.preventDefault(), void save());
      return;
    }
    if (typingIn(e.target)) return;
    const n = Number(e.key);
    if (Number.isInteger(n) && n >= 1 && n <= statuses.length) setPicked((p) => (p === statuses[n - 1] && mode === "override" ? null : statuses[n - 1]));
    else if (mode === "override" && e.key === "c") (setCorrect(true), setPicked(null));
    else if (mode === "override" && e.key === "x") (setCorrect(false), setPicked(null));
    else if (e.key === "Enter" && !(e.target instanceof HTMLButtonElement)) void save();
    else return;
    e.preventDefault();
  };

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent
        className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg"
        onKeyDown={onKeyDown}
        onCloseAutoFocus={(e) => {
          if (onClosed) (e.preventDefault(), onClosed());
        }}
      >
        <DialogHeader>
          <DialogTitle>{copy.title}</DialogTitle>
          <DialogDescription>{copy.help}</DialogDescription>
        </DialogHeader>
        <div className="bg-muted/50 grid gap-1 rounded-md border px-3 py-2 text-sm">
          <p>
            <span className="text-muted-foreground">{ruleName(rule.kind, rule.index)}:</span> <RuleText text={rule.text} />
          </p>
          <p>
            The checker said <Tag className={RULE_STATUS[rule.status].tone}>{RULE_STATUS[rule.status].label}</Tag>
            {rule.reason && <span className="text-muted-foreground"> {rule.reason}</span>}
          </p>
        </div>
        {mode === "override" && (
          <div className="grid grid-cols-2 gap-2">
            <Button variant="outline" aria-pressed={correct === true} className={cn(correct === true && "bg-brand-soft border-primary/40")} onClick={() => (setCorrect(true), setPicked(null))}>
              <kbd className="text-muted-foreground rounded border px-1 font-mono text-sm leading-4">c</kbd> <Check /> Correct
            </Button>
            <Button variant="outline" aria-pressed={correct === false} className={cn(correct === false && "bg-warn-soft border-warn/40")} onClick={() => (setCorrect(false), setPicked(null))}>
              <kbd className="text-muted-foreground rounded border px-1 font-mono text-sm leading-4">x</kbd> <X /> Incorrect
            </Button>
          </div>
        )}
        {statuses.length > 0 && (
          <div>
            <h4 className="mb-2 text-sm font-semibold">
              What actually happened? {mode === "override" && <span className="text-muted-foreground font-normal">Optional</span>}
            </h4>
            <StatusButtons statuses={statuses} picked={picked} keys onPick={(s) => setPicked(mode === "override" && picked === s ? null : s)} disabled={busy} />
          </div>
        )}
        {mode === "status" && picked && rule.finding && !isFinding(picked) && <p className="text-muted-foreground text-sm">This finding will be hidden from rates, as a finding marked incorrect is.</p>}
        <Textarea className="min-h-16 text-sm" placeholder="Note (optional)" aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} />
        <DialogFooter className="items-center">
          <span className="text-muted-foreground mr-auto hidden text-xs sm:inline">
            {mode === "override" ? "c / x, " : ""}1–{Math.max(statuses.length, 1)} pick · Enter saves · Esc closes
          </span>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!ready || busy} onClick={() => void save()}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The row's buttons: "Wrong?" (or the call made) on a finding, "Checker missed this" on a rule the checker passed,
 * "Wrong status" everywhere. `onOpen` opens the row's dialog. */
export function RuleCallButtons({ rule, onOpen, className, part }: { rule: RuleResultOut; onOpen: (m: CallMode) => void; className?: string; part: "finding" | "status" }) {
  const showStatusButtons = part === "status";
  const passed = !rule.finding && (rule.status === "done" || rule.status === "ok");
  return (
    <span className={cn("flex flex-wrap items-center", className)}>
      {part === "finding" && rule.finding && rule.finding_id && (
        rule.override != null ? (
          <button type="button" className="cursor-pointer" title="Change your call on this finding" onClick={() => onOpen("override")}>
            <Tag className={cn("border-transparent", rule.override ? "bg-brand-soft text-primary" : "bg-warn-soft text-warn")}>{rule.override ? "confirmed correct" : "marked incorrect"}</Tag>
          </button>
        ) : (
          <Button size="xs" variant="ghost" className="text-muted-foreground h-6 px-2" onClick={() => onOpen("override")}>
            Wrong?
          </Button>
        )
      )}
      {showStatusButtons && passed && (
        <Button size="xs" variant="ghost" className="text-err h-6 px-2" onClick={() => onOpen("missed")}>
          Checker missed this
        </Button>
      )}
      {showStatusButtons && (
        <Button size="xs" variant="ghost" className="text-muted-foreground h-6 px-2" onClick={() => onOpen("status")}>
          Wrong status
        </Button>
      )}
    </span>
  );
}

const SOURCE: Record<RuleCorrectionOut["source"], { to: string; right: string }> = {
  correction: { to: "Corrected to", right: "Confirmed: the checker was right" },
  spot_check: { to: "Spot check: actually", right: "Spot check: the checker was right" },
  override: { to: "What actually happened:", right: "Confirmed: the checker was right" },
};

/** What a reviewer said about the rule: the status, who and when, and what the checker had said. */
export function CorrectionNote({ rule, className }: { rule: RuleResultOut; className?: string }) {
  const c = rule.correction;
  if (!c) return null;
  const confirmed = c.status === rule.status;
  const missed = !rule.finding && isFinding(c.status);
  return (
    <div role="note" className={cn("flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border px-2 py-1 text-xs", missed ? "border-err/40 bg-err-soft/40" : "bg-muted/60", className)}>
      <span className="font-semibold">{missed ? "Checker missed this" : confirmed ? SOURCE[c.source].right : SOURCE[c.source].to}</span>
      {!confirmed && <Tag className={RULE_STATUS[c.status].tone}>{RULE_STATUS[c.status].label}</Tag>}
      <span className="text-muted-foreground">
        {c.reviewer ? `by ${c.reviewer}` : "by a reviewer"} · {when(c.created_at)}
        {!confirmed && <> · the checker said {statusPhrase(c.checked_status ?? rule.status)}</>}
      </span>
      {c.note && <span className="text-muted-foreground basis-full">“{c.note}”</span>}
    </div>
  );
}
