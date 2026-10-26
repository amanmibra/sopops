// Corrections on one rule of a session: "Wrong status" (any rule) and "Checker missed this" (a done step or an ok never
// rule), and the note a corrected rule shows: the status a reviewer gave, who and when.
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import type { RuleResultOut } from "@/lib/api";
import { when } from "@/lib/kinds";
import { KIND_STATUSES, MISSED_STATUSES, RULE_STATUS, type RuleCorrectionOut, type RuleStatus, isFinding, review, statusPhrase } from "@/lib/review";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { Tag } from "./bits";
import { author } from "./form";
import { Button } from "./ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
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

type Mode = "status" | "missed";

const COPY: Record<Mode, { trigger: string; title: string; help: string }> = {
  status: { trigger: "Wrong status", title: "What actually happened?", help: "Pick the status this rule should have. It's kept with the session and used to recalibrate the checker." },
  missed: {
    trigger: "Checker missed this",
    title: "What did the checker miss?",
    help: "The agent got this wrong and the checker passed it. It's shown here and used to recalibrate; the session's result and Insights keep what the checker found.",
  },
};

function CorrectionPopover({ mode, sessionId, procedureId, rule }: { mode: Mode; sessionId: string; procedureId: string; rule: RuleResultOut }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<RuleStatus | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const statuses = mode === "missed" ? MISSED_STATUSES[rule.kind] : KIND_STATUSES[rule.kind].filter((s) => s !== rule.status);
  const copy = COPY[mode];
  const save = async () => {
    if (!picked) return;
    setBusy(true);
    try {
      const r = await review.label(ws, sessionId, procedureId, rule.rule_id, { status: picked, note: note.trim() || null, reviewer: author(), source: "correction" });
      toast.success(r.missed ? `Saved: the checker missed this (${statusPhrase(picked)}).` : r.suppressed ? `Corrected to ${statusPhrase(picked)}. The finding is hidden from rates.` : `Corrected to ${statusPhrase(picked)}.`);
      setOpen(false);
      setPicked(null);
      setNote("");
      void qc.invalidateQueries({ queryKey: ["ws", ws, "session"] });
      void qc.invalidateQueries({ queryKey: ["ws", ws, "insights"] });
      void qc.invalidateQueries({ queryKey: ["ws", ws, "spot-checks"] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button size="xs" variant="ghost" className={cn("h-6 px-2", mode === "missed" ? "text-err" : "text-muted-foreground")}>
          {copy.trigger}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <h3 className="mb-1 text-sm font-semibold">{copy.title}</h3>
        <p className="text-muted-foreground mb-2 text-xs">
          The checker said <Tag className={RULE_STATUS[rule.status].tone}>{RULE_STATUS[rule.status].label}</Tag>. {copy.help}
        </p>
        <div className="mb-2">
          <StatusButtons statuses={statuses} picked={picked} onPick={setPicked} disabled={busy} />
        </div>
        {picked && rule.finding && !isFinding(picked) && <p className="text-muted-foreground mb-2 text-xs">This finding will be hidden from rates, as a finding marked incorrect is.</p>}
        <Textarea className="mb-2 min-h-12 text-sm" placeholder="Note (optional)" aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} />
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button size="sm" disabled={!picked || busy} onClick={() => void save()}>
            Save
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

/** The controls on a rule row: "Wrong status" everywhere, and "Checker missed this" on a rule the checker passed. The
 * session page shows them on hover and on the selected rule. */
export function RuleCorrectionControls({ sessionId, procedureId, rule, className }: { sessionId: string; procedureId: string; rule: RuleResultOut; className?: string }) {
  const passed = !rule.finding && (rule.status === "done" || rule.status === "ok");
  return (
    <span className={cn("flex flex-wrap items-center", className)}>
      {passed && <CorrectionPopover mode="missed" sessionId={sessionId} procedureId={procedureId} rule={rule} />}
      <CorrectionPopover mode="status" sessionId={sessionId} procedureId={procedureId} rule={rule} />
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
