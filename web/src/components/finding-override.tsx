// "Wrong?" on one finding: the reviewer says whether the checker got it right, what actually happened (optional), and
// a note.
import { useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { type FindingOverride, KIND_STATUSES, type RuleKind, type RuleStatus, isFinding, review, statusPhrase } from "@/lib/review";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { Tag } from "./bits";
import { author } from "./form";
import { StatusButtons } from "./rule-correction";
import { Button } from "./ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import { Textarea } from "./ui/textarea";

/** `override`: the reviewer's call so far (true: the checker was right), or none. `kind`: the rule's, for the statuses
 * "What actually happened?" offers. */
export function FindingOverrideControl({ findingId, override, kind }: { findingId: string; override?: boolean | null; kind: RuleKind }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [correct, setCorrect] = useState<boolean | null>(override ?? null);
  const [status, setStatus] = useState<RuleStatus | null>(null);
  const [note, setNote] = useState("");
  // a right finding happened as some finding status (out of order rather than skipped); a wrong one as something else
  const statuses = correct == null ? [] : KIND_STATUSES[kind].filter((s) => isFinding(s) === correct);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<boolean | null>(override ?? null);
  const save = async () => {
    if (correct == null) return;
    setBusy(true);
    try {
      const r: FindingOverride = await review.override(ws, findingId, { correct, status, note: note.trim() || null, reviewer: author() });
      setSaved(r.correct);
      const what = status ? ` What happened: ${statusPhrase(status)}.` : "";
      toast.success(correct ? `Marked correct.${what}` : `Marked incorrect. It's hidden from rates and used to recalibrate.${what}`);
      setOpen(false);
      void qc.invalidateQueries({ queryKey: ["ws", ws, "session"] });
      void qc.invalidateQueries({ queryKey: ["ws", ws, "insights"] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        {saved != null ? (
          <button type="button" className="cursor-pointer" title="Change your call on this finding">
            <Tag className={cn("border-transparent", saved ? "bg-brand-soft text-primary" : "bg-warn-soft text-warn")}>{saved ? "confirmed correct" : "marked incorrect"}</Tag>
          </button>
        ) : (
          <Button size="xs" variant="ghost" className="text-muted-foreground h-6 px-2">
            Wrong?
          </Button>
        )}
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80">
        <h3 className="mb-1 text-sm font-semibold">Is this finding right?</h3>
        <p className="text-muted-foreground mb-2 text-xs">Incorrect findings are hidden from rates and used to recalibrate the checker.</p>
        <div className="mb-2 grid grid-cols-2 gap-2">
          <Button size="sm" variant="outline" aria-pressed={correct === true} className={cn(correct === true && "bg-brand-soft border-primary/40")} onClick={() => (setCorrect(true), setStatus(null))}>
            <Check /> Correct
          </Button>
          <Button size="sm" variant="outline" aria-pressed={correct === false} className={cn(correct === false && "bg-warn-soft border-warn/40")} onClick={() => (setCorrect(false), setStatus(null))}>
            <X /> Incorrect
          </Button>
        </div>
        {statuses.length > 0 && (
          <div className="mb-2">
            <h4 className="mb-1 text-xs font-semibold">
              What actually happened? <span className="text-muted-foreground font-normal">Optional</span>
            </h4>
            <StatusButtons statuses={statuses} picked={status} onPick={(s) => setStatus(status === s ? null : s)} />
          </div>
        )}
        <Textarea className="mb-2 min-h-12 text-sm" placeholder="Note (optional)" aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} />
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button size="sm" disabled={correct == null || busy} onClick={() => void save()}>
            Save
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
