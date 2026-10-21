// A rule's wordings on its Insights page: which rule each rewording replaced and how sopops knew, with controls to
// fix it by hand. Linking says a rule is a rewording of another (their findings, verdicts and history join);
// unlinking says a wording is a new rule (it leaves with its findings). Both ask first.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, Loader2, Unlink } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { type InsightFilters, type LineageRule, call, q, sdk } from "@/lib/api";
import { ruleName } from "@/lib/insights";
import { plural } from "@/lib/kinds";
import { useWs } from "@/lib/workspace";
import { ErrorBanner, RuleText } from "../bits";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";
import { Hint, TipBody, WidgetCard } from "./kit";

const lineageQuery = (ws: string, procedureId: string, ruleId: string) => ({
  queryKey: ["ws", ws, "lineage", procedureId, ruleId],
  queryFn: () => call(sdk.getRuleLineage({ path: { workspace: ws, rule_id: ruleId }, query: { procedure: procedureId } })),
});

/** How sopops knew a wording replaced the one before it, in a sentence. */
function how(r: LineageRule): string {
  const at = r.release_id != null ? ` in release ${r.release_id}` : "";
  if (r.how === "suggestion") return `Reworded by an applied suggestion${at}.`;
  if (r.how === "matched") return `Matched when it was published${at}${r.similarity != null ? `: ${Math.round(r.similarity * 100)}% of the words are the same` : ""}.`;
  if (r.how === "manual") return "Linked by hand.";
  return "The first wording.";
}

type Pending = { kind: "unlink"; rule: LineageRule } | { kind: "link"; previous: string } | null;

export function LineageCard({ procedureId, ruleId, kind, filters }: { procedureId: string; ruleId: string; kind: string | null | undefined; filters: InsightFilters }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const lin = useQuery(lineageQuery(ws, procedureId, ruleId));
  const ladder = useQuery(q.insightsProcedure(ws, procedureId, filters));
  const [picked, setPicked] = useState<string>("");
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const d = lin.data;
  const rules = d?.rules ?? [];
  const others = (ladder.data?.rules ?? []).filter((r) => r.kind === kind && r.lineage_id !== d?.lineage_id);
  const previous = pending?.kind === "link" ? others.find((r) => r.rule_id === pending.previous) : undefined;

  const run = async () => {
    if (!pending || !d) return;
    setBusy(true);
    setError(null);
    try {
      if (pending.kind === "unlink") {
        await call(sdk.unlinkRule({ path: { workspace: ws, rule_id: pending.rule.rule_id }, query: { procedure: procedureId } }));
        toast.success("Unlinked. That wording is its own rule now, with its own pattern.");
      } else {
        await call(sdk.linkRule({ path: { workspace: ws, rule_id: d.lineage_id }, body: { previous_rule_id: pending.previous, procedure_id: procedureId } }));
        toast.success("Linked. Their findings, verdicts and history are one rule's now.");
      }
      setPending(null);
      setPicked("");
      await qc.invalidateQueries({ queryKey: ["ws", ws] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <WidgetCard
      title="Wordings"
      subtitle={rules.length > 1 ? `${plural(rules.length, "wording", "wordings")}, oldest first. Results, patterns and verdicts carry over from one to the next.` : "One wording so far."}
      help="When a rule's text changes, sopops works out which rule the new text replaced: from the suggestion that made the edit, else by matching the words. If it got that wrong, link or unlink them here."
      loading={lin.isPending}
      error={d ? undefined : lin.error}
      onRetry={() => void lin.refetch()}
      flush
    >
      <ol className="divide-y">
        {rules.map((r, i) => (
          <li key={r.rule_id} className="px-4 py-2 text-sm">
            <div className="flex items-start justify-between gap-3">
              <span className="min-w-0">“{r.text ? <RuleText text={r.text} /> : "Not run by any session yet"}”</span>
              {i > 0 && (
                <Button size="xs" variant="ghost" className="shrink-0" onClick={() => setPending({ kind: "unlink", rule: r })} title="Say this wording is a new rule, not a rewording">
                  <Unlink /> Not the same rule
                </Button>
              )}
            </div>
            <div className="text-muted-foreground mt-0.5 text-xs">
              {r.from_release != null ? `From release ${r.from_release}. ` : ""}
              {how(r)}
            </div>
          </li>
        ))}
      </ol>
      {d && others.length > 0 && (
        <div className="border-t px-4 py-3">
          <p className="text-muted-foreground mb-2 text-sm">
            Is this a rewording of another {kind === "step" ? "step" : kind === "never" ? "never rule" : "rule"} that sopops didn't match? Link them, and their findings, verdicts and history become one rule's.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Select value={picked} onValueChange={setPicked}>
              <SelectTrigger size="sm" className="w-full max-w-[420px] min-w-0">
                <SelectValue placeholder="The rule it replaced…" />
              </SelectTrigger>
              <SelectContent>
                {others.map((r) => (
                  <SelectItem key={r.rule_id} value={r.rule_id}>
                    <span className="truncate">
                      {ruleName(r.kind, r.index)}: <RuleText text={r.text} />
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Hint focusable={false} tip={<TipBody head="Link">This rule replaced the one you picked. You'll be asked to confirm.</TipBody>}>
              <Button size="sm" variant="outline" disabled={!picked} onClick={() => setPending({ kind: "link", previous: picked })}>
                <Link2 /> Link
              </Button>
            </Hint>
          </div>
        </div>
      )}
      <Dialog open={!!pending} onOpenChange={(open) => !open && !busy && setPending(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{pending?.kind === "unlink" ? "Treat this wording as a new rule?" : "Link these two rules?"}</DialogTitle>
            <DialogDescription asChild>
              {pending?.kind === "unlink" ? (
                <div className="grid gap-2">
                  <p>“<RuleText text={pending.rule.text} />”</p>
                  <p>
                    It stops counting as a rewording of the wording before it. Its findings, and those of any later wording, move to a pattern of their own with no verdict, and Insights shows it as a separate rule. You can
                    link it back later.
                  </p>
                </div>
              ) : (
                <div className="grid gap-2">
                  <p>
                    This rule replaced “{previous?.text ?? "the rule you picked"}”{previous ? ` (${ruleName(previous.kind, previous.index)})` : ""}.
                  </p>
                  <p>
                    Their findings join one pattern. If both have patterns, they merge: failure modes, verdicts (the latest counts) and suggestions move over. Insights counts them as one rule. You can unlink them
                    later.
                  </p>
                </div>
              )}
            </DialogDescription>
          </DialogHeader>
          {error != null && <ErrorBanner error={error} lead="That didn't work:" />}
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={() => setPending(null)}>
              Cancel
            </Button>
            <Button disabled={busy} onClick={() => void run()}>
              {busy && <Loader2 className="animate-spin" />} {pending?.kind === "unlink" ? "Unlink" : "Link them"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </WidgetCard>
  );
}
