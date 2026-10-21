// What the models cost: QA → Insights' "Model spend" card (total, per session, by purpose), a session's own cost, and
// Settings' price table. Costs are estimates from the price table in use; GET …/usage defines every number.
import { useQuery } from "@tanstack/react-query";
import type { InsightFilters, PurposeSpend, SpendTotals } from "@/lib/api";
import { q } from "@/lib/api";
import { windowPhrase } from "@/lib/insights";
import { plural } from "@/lib/kinds";
import { useWs } from "@/lib/workspace";
import { Panel } from "../bits";
import { Hint, Skel, TipBody, WidgetCard } from "./kit";

/** Dollars: cents for a dollar or more, else enough digits to read a fraction of a cent. */
export function usd(x: number | null | undefined): string {
  if (x == null) return "—";
  if (x === 0) return "$0";
  if (x >= 1) return `$${x.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return `$${x.toPrecision(2).replace(/0+$/, "").replace(/\.$/, "")}`;
}

const tokens = (n: number) => new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(n);

const PURPOSE: Record<PurposeSpend["purpose"], { label: string; help: string }> = {
  route: { label: "Routing", help: "Which procedures applied to each session." },
  check: { label: "Checks", help: "Each rule's decisions, and finding the quotes that show them." },
  review: { label: "Reviewer", help: "The reviewer model on the rules the decisions flagged or weren't sure about." },
  cluster: { label: "Patterns", help: "Sorting findings into patterns, splitting rules into failure modes and naming them." },
  theme: { label: "Themes", help: "Grouping a procedure's patterns by the phase of the call." },
  suggestion: { label: "Suggestions", help: "Writing suggested edits from verdicts." },
  calibrate: { label: "Calibration", help: "Fitting the check thresholds to your labels." },
  onboarding: { label: "Import", help: "Extracting procedures from existing instructions." },
};

function callsTip(t: SpendTotals) {
  return `${plural(t.calls, "model call", "model calls")}: ${tokens(t.input_tokens)} tokens in, ${tokens(t.output_tokens)} out.${t.unpriced_calls ? ` ${plural(t.unpriced_calls, "call was", "calls were")} to a model with no price, so ${t.unpriced_calls === 1 ? "it isn't" : "they aren't"} in the cost.` : ""}`;
}

function Stat({ label, value, tip }: { label: string; value: string; tip: React.ReactNode }) {
  return (
    <Hint tip={tip} focusable={false}>
      <span className="grid min-w-[7.5rem] gap-0.5">
        <span className="text-muted-foreground text-xs">{label}</span>
        <span className="text-lg font-semibold tabular-nums">{value}</span>
      </span>
    </Hint>
  );
}

export function SpendCard({ filters }: { filters: InsightFilters }) {
  const { ws } = useWs();
  const res = useQuery(q.usage(ws, filters));
  const d = res.data;
  const top = Math.max(...(d?.by_purpose.map((p) => p.cost_usd) ?? [0]), 0);
  return (
    <WidgetCard
      title="Model spend"
      subtitle={`What the models cost your team over ${windowPhrase(filters)}, estimated from the price table (Settings).`}
      help="Every model call sopops made: routing, checks, the reviewer, patterns, themes, suggestions, calibration. Counted for the whole team, whatever the agent or procedure filter. Cached answers cost nothing and aren't counted."
      loading={res.isPending}
      error={d ? undefined : res.error}
      onRetry={() => void res.refetch()}
      empty={!!d && d.total.calls === 0}
      emptyMessage="No model calls in this time range."
    >
      {d && (
        <div className="grid gap-4 md:grid-cols-[auto_minmax(0,1fr)]">
          <div className="flex flex-wrap content-start gap-x-6 gap-y-3">
            <Stat label="Total" value={usd(d.total.cost_usd)} tip={<TipBody head={`${usd(d.total.cost_usd)} in total`}>{callsTip(d.total)}</TipBody>} />
            <Stat
              label="Per session"
              value={usd(d.per_session_usd)}
              tip={
                <TipBody head={`${usd(d.per_session_usd)} per session`}>
                  The calls about one session (routing, checks, the reviewer, sorting its findings), averaged over the {plural(d.sessions, "session", "sessions")} they were about. Patterns, themes, suggestions and calibration aren't in it.
                </TipBody>
              }
            />
            <Stat label="Model calls" value={tokens(d.total.calls)} tip={<TipBody head={plural(d.total.calls, "model call", "model calls")}>{callsTip(d.total)}</TipBody>} />
          </div>
          <ul className="grid content-start gap-2" aria-label="Cost by purpose">
            {d.by_purpose.map((p) => {
              const m = PURPOSE[p.purpose];
              return (
                <li key={p.purpose}>
                  <Hint focusable={false} tip={<TipBody head={`${m.label}: ${usd(p.cost_usd)}`}>{m.help} {callsTip(p)}</TipBody>}>
                    <span className="grid grid-cols-[6.5rem_minmax(0,1fr)_4.5rem] items-center gap-2 text-sm">
                      <span className="truncate">{m.label}</span>
                      <span className="bg-muted h-2 overflow-hidden rounded-full" aria-hidden>
                        <span className="bg-primary block h-full rounded-full" style={{ width: `${top ? Math.max(2, (100 * p.cost_usd) / top) : 0}%` }} />
                      </span>
                      <span className="text-right tabular-nums">{usd(p.cost_usd)}</span>
                    </span>
                  </Hint>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </WidgetCard>
  );
}

/** A session's model cost, for its page. */
export function SessionCost({ calls, cost }: { calls: number; cost: number | null | undefined }) {
  if (!calls) return null;
  return (
    <Hint
      focusable={false}
      tip={<TipBody head={`${usd(cost)} in model calls`}>{plural(calls, "model call was", "model calls were")} about this session: routing, its checks, the reviewer and sorting its findings. Estimated from the price table (Settings).</TipBody>}
    >
      <span className="tabular-nums">{usd(cost)} in model calls</span>
    </Hint>
  );
}

/** Settings: the price table costs are estimated with, and the price each configured model gets. */
export function PricesCard() {
  const { ws } = useWs();
  const res = useQuery(q.prices(ws));
  const d = res.data;
  const per = (x: number | null | undefined) => (x == null ? "—" : `$${x}`);
  return (
    <Panel title="Model prices">
      <p className="text-muted-foreground mb-3 text-sm">
        What Model spend in Insights is estimated with, in dollars per million tokens. Set <code className="text-xs">{d?.env ?? "SOPOPS_MODEL_PRICES"}</code> to change a price or add a model, as JSON:{" "}
        <code className="text-xs">{'{"openai/gpt-5-mini": [0.25, 2]}'}</code>.
      </p>
      {!d ? (
        <Skel className="h-24 w-full" />
      ) : (
        <div className="grid gap-3">
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-muted-foreground text-left text-xs">
                <tr>
                  <th className="px-2 py-2 font-medium">In use</th>
                  <th className="px-2 py-2 font-medium">Model</th>
                  <th className="px-2 py-2 text-right font-medium">Input</th>
                  <th className="px-2 py-2 text-right font-medium">Output</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {d.roles.map((r) => (
                  <tr key={r.role}>
                    <td className="px-2 py-2">{r.role === "decide" ? "Decisions" : r.role === "fast" ? "Fast" : "Reviewer"}</td>
                    <td className="px-2 py-2 font-mono text-xs">{r.model ?? <span className="text-muted-foreground font-sans">not set</span>}</td>
                    <td className="px-2 py-2 text-right tabular-nums">
                      <Hint focusable={false} tip={r.input_per_m == null ? (r.model ? "No price for this model: its calls count, their cost doesn't." : "No model.") : `$${r.input_per_m} per million input tokens.`}>
                        <span>{per(r.input_per_m)}</span>
                      </Hint>
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums">
                      <Hint focusable={false} tip={r.output_per_m == null ? "—" : `$${r.output_per_m} per million output tokens.`}>
                        <span>{per(r.output_per_m)}</span>
                      </Hint>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="text-sm">
            <h3 className="text-muted-foreground mb-1 text-xs font-medium">The price table ({plural(d.prices.length, "model", "models")})</h3>
            <ul className="grid gap-1">
              {d.prices.map((p) => (
                <li key={p.model} className="flex flex-wrap justify-between gap-x-3 tabular-nums">
                  <span className="font-mono text-xs">
                    {p.model}
                    {p.source === "env" && <span className="text-muted-foreground font-sans"> (from {d.env})</span>}
                  </span>
                  <Hint focusable={false} tip={`$${p.input_per_m} per million input tokens, $${p.output_per_m} per million output tokens.`}>
                    <span>
                      {per(p.input_per_m)} in · {per(p.output_per_m)} out
                    </span>
                  </Hint>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Panel>
  );
}
