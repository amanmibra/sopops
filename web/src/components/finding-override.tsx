// "Wrong?" on one finding: the reviewer says whether the checker got it right, with a note.
import { useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { type FindingOverride, review } from "@/lib/review";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { Tag } from "./bits";
import { author } from "./form";
import { Button } from "./ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import { Textarea } from "./ui/textarea";

/** `override`: the reviewer's call so far (true: the checker was right), or none. */
export function FindingOverrideControl({ findingId, override }: { findingId: string; override?: boolean | null }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [correct, setCorrect] = useState<boolean | null>(override ?? null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<boolean | null>(override ?? null);
  const save = async () => {
    if (correct == null) return;
    setBusy(true);
    try {
      const r: FindingOverride = await review.override(ws, findingId, { correct, note: note.trim() || null, reviewer: author() });
      setSaved(r.correct);
      toast.success(correct ? "Marked correct." : "Marked incorrect. It's hidden from rates and used to recalibrate.");
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
          <Button size="xs" variant="ghost" className="text-muted-foreground h-6 px-1.5">
            Wrong?
          </Button>
        )}
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80">
        <h3 className="mb-1 text-[13px] font-semibold">Is this finding right?</h3>
        <p className="text-muted-foreground mb-2 text-xs">Incorrect findings are hidden from rates and used to recalibrate the checker.</p>
        <div className="mb-2 grid grid-cols-2 gap-1.5">
          <Button size="sm" variant="outline" aria-pressed={correct === true} className={cn(correct === true && "bg-brand-soft border-primary/40")} onClick={() => setCorrect(true)}>
            <Check /> Correct
          </Button>
          <Button size="sm" variant="outline" aria-pressed={correct === false} className={cn(correct === false && "bg-warn-soft border-warn/40")} onClick={() => setCorrect(false)}>
            <X /> Incorrect
          </Button>
        </div>
        <Textarea className="mb-2 min-h-12 text-[13px]" placeholder="Note (optional)" aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} />
        <div className="flex justify-end gap-1.5">
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
