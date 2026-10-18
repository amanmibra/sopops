// Settings → Calibration: fit each procedure's check thresholds to the reviewers' labels (verdicts and overrides).
// Preview runs it without saving; Apply saves the new thresholds. Refusals (too few labels) are said plainly.
import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError, type Refusal } from "@/lib/api";
import { ruleName } from "@/lib/insights";
import { plural } from "@/lib/kinds";
import { type CalibratedProcedure, type CalibratedRule, review } from "@/lib/review";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { Banner, ConfirmButton, ErrorBanner, Panel } from "./bits";
import { Hint, TipBody } from "./insights/kit";
import { Button } from "./ui/button";

/** Why a procedure was refused, in plain words (from the refusal's code; its message otherwise). */
export function refusal(r: Refusal): string {
  const again = "Give verdicts on more of this procedure's patterns, then try again.";
  if (r.code === "too_few_labeled_sessions" && r.have != null && r.need != null)
    return `Not enough labels yet: ${plural(r.have, "session has", "sessions have")} a reviewed finding, and it needs ${r.need}. ${again}`;
  if (r.code === "too_few_failures" && r.need != null) return `Not enough labels yet: no rule has ${r.need} findings judged. ${again}`;
  if (r.code === "not_in_release") return `${r.message} Calibrate after a session on a release that has it.`;
  return r.message;
}

/** Why one question kept its threshold. */
function ruleSkip(r: Refusal): string {
  if (r.code === "too_few_failures" && r.have != null && r.need != null) return `Kept: only ${r.have} judged findings, needs ${r.need}`;
  if (r.code === "too_alike") return "Kept: the checker's answers are too alike to fit";
  if (r.code === "held_out") return "Kept: the new value didn't hold up on held-out sessions";
  return `Kept: ${r.message}`;
}

const fmt = (x: number | null | undefined) => (x == null ? "—" : x.toFixed(2));

type Run = { busy?: "preview" | "apply"; result?: CalibratedProcedure; error?: unknown };

export function CalibrationCard() {
  const { ws, cfg } = useWs();
  const qc = useQueryClient();
  const [runs, setRuns] = useState<Record<string, Run>>({});
  const procs = [...cfg.procedures].sort((a, b) => a.name.localeCompare(b.name));

  const run = async (pid: string, dry: boolean) => {
    setRuns((r) => ({ ...r, [pid]: { ...r[pid], busy: dry ? "preview" : "apply", error: undefined } }));
    try {
      const out = await review.calibrate(ws, { procedure: pid, dry_run: dry });
      const result = out.procedures.find((p) => p.procedure_id === pid) ?? out.procedures[0];
      setRuns((r) => ({ ...r, [pid]: { result } }));
      if (!dry && result?.saved) {
        toast.success("New thresholds saved. Checks from now on use them.");
        void qc.invalidateQueries({ queryKey: ["ws", ws] });
      }
    } catch (e) {
      setRuns((r) => ({ ...r, [pid]: { error: e } }));
    }
  };

  return (
    <Panel title="Calibration">
      <p className="text-muted-foreground mb-3 text-[13px]">
        Tune how strict the checker is on each procedure, using your verdicts and “wrong?” marks as the answer key. Preview shows what would change; Apply saves it. It needs at least 5 sessions with a reviewed finding, and
        one rule with 3 judged findings.
      </p>
      {!procs.length ? (
        <p className="text-muted-foreground text-[13px]">No procedures yet.</p>
      ) : (
        <ul className="divide-y rounded-lg border">
          {procs.map((p) => {
            const r = runs[p.id] ?? {};
            return (
              <li key={p.id} className="px-3 py-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 font-medium">{p.name}</span>
                  <Button size="sm" variant="outline" disabled={!!r.busy} onClick={() => void run(p.id, true)} title="Fit the thresholds to your labels without saving">
                    {r.busy === "preview" && <Loader2 className="animate-spin" />} Preview
                  </Button>
                  <ConfirmButton size="sm" disabled={!!r.busy} confirm="Save new thresholds?" onConfirm={() => void run(p.id, false)} title="Fit the thresholds and save them">
                    {r.busy === "apply" && <Loader2 className="animate-spin" />} Apply
                  </ConfirmButton>
                </div>
                {r.busy && <p className="text-muted-foreground mt-1.5 text-[13px]" role="status">Fitting to your labels. This can take a minute.</p>}
                {r.error != null &&
                  (r.error instanceof ApiError && r.error.status >= 500 ? (
                    <Banner tone="err" className="mt-2 mb-0 text-[13px]">
                      Calibration failed on the server, so nothing changed. Try again; if it keeps failing, the server log says why.
                    </Banner>
                  ) : (
                    <div className="mt-2">
                      <ErrorBanner error={r.error} />
                    </div>
                  ))}
                {r.result && <Result r={r.result} />}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

function Result({ r }: { r: CalibratedProcedure }) {
  if (r.skipped)
    return (
      <Banner tone="warn" className="mt-2 mb-0 text-[13px]">
        {refusal(r.skipped)}
      </Banner>
    );
  const label = (x: CalibratedRule) => {
    if (!x.rule_id) return "Overall: does the session need attention?";
    return x.kind ? `${ruleName(x.kind, x.index)}${x.text ? `: ${x.text}` : ""}` : x.field;
  };
  const up = r.score != null && r.score_before != null ? r.score - r.score_before : null;
  return (
    <div className="mt-2.5 grid gap-2.5">
      <div className="flex flex-wrap gap-2">
        <Stat label="Labels used" value={`${r.labels}`} tip={<TipBody head="Labels used">{plural(r.labels, "session with a reviewed finding", "sessions with a reviewed finding")}; {plural(r.negative_labels, "finding was", "findings were")} judged wrong.</TipBody>} />
        <Stat
          label="Score"
          value={
            <span className="inline-flex items-center gap-1">
              {fmt(r.score_before)} <ArrowRight className="size-3" /> {fmt(r.score)}
            </span>
          }
          tone={up == null ? undefined : up > 0 ? "ok" : up < 0 ? "err" : undefined}
          tip={<TipBody head="Score, before and after">How well the checker agrees with your labels, from 0 to 1: catching the sessions that need attention, and blaming the right rules. Higher is better.</TipBody>}
        />
        <Stat
          label={r.saved ? "Saved" : "Preview"}
          value={r.saved ? "In use now" : "Not saved"}
          tip={r.saved ? "The new thresholds are saved. Checks from now on use them." : "Nothing was saved. Apply to use these thresholds."}
        />
      </div>
      {r.rules.length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-[13px]">
            <thead className="bg-muted/50 text-muted-foreground text-left text-xs">
              <tr>
                <th className="px-2.5 py-1.5 font-medium">Question</th>
                <th className="px-2.5 py-1.5 text-right font-medium">
                  <Hint tip="The threshold in use now: calibrated earlier, or the default.">
                    <span className="cursor-help border-b border-dotted border-current">Before</span>
                  </Hint>
                </th>
                <th className="px-2.5 py-1.5 text-right font-medium">
                  <Hint tip="The threshold fitted to your labels. Higher means the checker must be surer before it flags.">
                    <span className="cursor-help border-b border-dotted border-current">After</span>
                  </Hint>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {r.rules.map((x) => {
                const d = x.after - x.before;
                return (
                  <tr key={x.field}>
                    <td className="px-2.5 py-1.5">
                      <span className="line-clamp-2">{label(x)}</span>
                      {x.skipped && <span className="text-muted-foreground block text-xs">{ruleSkip(x.skipped)}</span>}
                    </td>
                    <td className="text-muted-foreground px-2.5 py-1.5 text-right tabular-nums">
                      <Hint focusable={false} tip={`In use now: the checker flags this when it's at least ${fmt(x.before)} sure.`}>
                        <span>{fmt(x.before)}</span>
                      </Hint>
                    </td>
                    <td className={cn("px-2.5 py-1.5 text-right font-medium tabular-nums whitespace-nowrap", Math.abs(d) < 0.005 && "text-muted-foreground font-normal")}>
                      <Hint
                        focusable={false}
                        tip={Math.abs(d) < 0.005 ? "Unchanged." : `Fitted to your labels: ${d > 0 ? "stricter" : "looser"} by ${Math.abs(d).toFixed(2)}, so it flags ${d > 0 ? "less" : "more"} often.`}
                      >
                        <span>
                          {fmt(x.after)}
                          {Math.abs(d) >= 0.005 && <span className="text-muted-foreground ml-1 text-xs font-normal">({d > 0 ? "+" : ""}{d.toFixed(2)})</span>}
                        </span>
                      </Hint>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, tip, tone }: { label: string; value: React.ReactNode; tip: React.ReactNode; tone?: "ok" | "err" }) {
  return (
    <Hint tip={tip}>
      <span className="bg-muted/50 inline-flex flex-col rounded-md border px-2.5 py-1">
        <span className="text-muted-foreground text-[11px]">{label}</span>
        <span className={cn("text-[13.5px] font-semibold tabular-nums", tone === "ok" && "text-ok", tone === "err" && "text-err")}>{value}</span>
      </span>
    </Hint>
  );
}
