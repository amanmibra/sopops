// The Patterns page's Open, Fixing and Fixed tabs (a card per pattern, with what's next for it), and a pattern's
// "where it stands" timeline: verdict, fix written, applied or sent as a pull request, release, held or came back.
import { useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, GitPullRequest, Sparkles } from "lucide-react";
import { type ReactNode, useState } from "react";
import type { PatternDetail } from "@/lib/api";
import { ruleName } from "@/lib/insights";
import { plural, when } from "@/lib/kinds";
import { type PatternsTab, REVIEW_STATE, type ReviewItem, type SuggestionSummary, type VerdictOut, review, summaryOf, verdictPhrase } from "@/lib/review";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ErrorBanner, Note } from "./bits";
import { HrefLink } from "./href-link";
import { Hint, Skel, TipBody } from "./insights/kit";
import { ConflictTag, ReviewStateChip, SuggestionState, SuggestionStatusTag, VerdictChip, ago } from "./review-bits";
import { Button } from "./ui/button";

/** Sessions so far toward a fix holding (once its release is live). */
const progressOf = (x: { fix_sessions?: number | null; fix_sessions_needed?: number | null }) =>
  x.fix_sessions != null && x.fix_sessions_needed != null ? { n: x.fix_sessions, of: x.fix_sessions_needed } : null;

/** The suggestion that belongs to the current verdict (written after it), if any. */
const fixFor = (s: SuggestionSummary | null | undefined, v: VerdictOut | null | undefined) => (s && (!v || s.created_at >= v.created_at) ? s : null);

const EMPTY: Record<Exclude<PatternsTab, "review" | "all">, { head: string; body: string }> = {
  open: { head: "Nothing open", body: "Real mistakes and wrong SOPs wait here until a fix is applied." },
  fixing: { head: "No fixes in progress", body: "When a fix for a Real mistake or SOP is wrong verdict is applied or sent as a pull request, it waits here for its release and 10 sessions on it." },
  fixed: { head: "No fixes confirmed yet", body: "A fix for a Real mistake or SOP is wrong verdict counts as held after 10 sessions on its release with no findings in the pattern." },
};

export function StateList({ tab, items, loading, error, onRetry }: { tab: Exclude<PatternsTab, "review" | "all">; items: ReviewItem[]; loading: boolean; error: unknown; onRetry: () => void }) {
  if (loading)
    return (
      <div className="grid gap-3" aria-busy>
        {[0, 1].map((i) => (
          <div key={i} className="bg-card grid gap-2 rounded-lg border p-4">
            <Skel className="h-4 w-1/3" />
            <Skel className="h-5 w-2/3" />
            <Skel className="h-8 w-1/2" />
          </div>
        ))}
      </div>
    );
  if (error)
    return (
      <Banner tone="err">
        Couldn't load these patterns. {error instanceof Error ? error.message : ""}{" "}
        <Button size="xs" variant="outline" onClick={onRetry}>
          Try again
        </Button>
      </Banner>
    );
  if (!items.length)
    return (
      <div role="status" className="bg-card text-muted-foreground flex flex-col items-center gap-1.5 rounded-lg border px-4 py-10 text-center text-[13px]">
        <div className="text-foreground font-semibold">{EMPTY[tab].head}</div>
        <p className="max-w-[56ch]">{EMPTY[tab].body}</p>
      </div>
    );
  return (
    <div className="grid gap-3">
      {items.map((it) => (
        <StateCard key={it.pattern_id} item={it} />
      ))}
    </div>
  );
}

function StateCard({ item }: { item: ReviewItem }) {
  const { ws, cfg } = useWs();
  const proc = item.procedure_name ?? cfg.procedures.find((p) => p.id === item.procedure_id)?.name ?? item.procedure_id;
  const v = item.verdict;
  return (
    <article className="bg-card min-w-0 rounded-lg border">
      <div className="px-4 py-3">
        <div className="text-muted-foreground mb-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs">
          <span className="text-k-procedure font-medium">{proc}</span>
          {item.theme_name && (
            <>
              <span aria-hidden>·</span>
              <span>{item.theme_name}</span>
            </>
          )}
          {item.kind !== "rule" && (
            <>
              <span aria-hidden>·</span>
              <span>{item.kind === "other" ? "unnamed findings" : "failure mode"}</span>
            </>
          )}
          <span className="ml-auto flex items-center gap-1.5">
            <VerdictChip v={v} />
            <ReviewStateChip state={item.review_state} note={item.review_note} />
          </span>
        </div>
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <h3 className="min-w-0 text-[15px] leading-snug font-semibold">
            <HrefLink href={wsHref(ws, "patterns", item.pattern_id)} className="text-foreground">
              {item.name}
            </HrefLink>
          </h3>
          <Hint focusable={false} tip="Sessions with a finding in it, and its findings, all time.">
            <span className="text-muted-foreground text-xs tabular-nums">
              {plural(item.sessions, "session", "sessions")} · {plural(item.findings, "finding", "findings")}
            </span>
          </Hint>
        </div>
        {item.rule?.text && (
          <p className="text-muted-foreground mt-1 line-clamp-2 text-[13px]">
            {ruleName(item.rule.kind, item.rule.index)}: {item.rule.text}
          </p>
        )}
        {v && (
          <p className="mt-2 text-[13px]">
            <span className="text-muted-foreground">
              Marked {verdictPhrase(v.verdict)}
              {v.inherited ? " on the rule" : ""} by {v.reviewer ?? "someone"}, {ago(v.created_at)}
            </span>
            {v.note && <span className="block">“{v.note}”</span>}
          </p>
        )}
      </div>
      <div className="bg-muted/40 border-t px-4 py-2.5">
        {item.review_state === "open" ? <OpenNext item={item} /> : item.review_state === "fixing" ? <FixingNext item={item} /> : <FixedNext item={item} />}
      </div>
    </article>
  );
}

function SuggestionLink({ s }: { s: SuggestionSummary }) {
  const { ws } = useWs();
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
      <SuggestionStatusTag status={s.status} />
      <ConflictTag freshness={s.freshness} />
      <HrefLink href={wsHref(ws, "suggestions", s.suggestion_id)} className="min-w-0 truncate font-medium">
        {summaryOf(s) ?? "The fix"}
      </HrefLink>
      {s.pr_url && (
        <a href={s.pr_url} target="_blank" rel="noreferrer" className="text-primary inline-flex items-center gap-1 hover:underline">
          <GitPullRequest className="size-3.5" /> Pull request <ExternalLink className="size-3" />
        </a>
      )}
    </span>
  );
}

/** Open: write a fix, or go to the one written. */
function OpenNext({ item }: { item: ReviewItem }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [asked, setAsked] = useState<SuggestionSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const s = fixFor(item.suggestion, item.verdict);
  const write = async () => {
    setBusy(true);
    setError(null);
    try {
      setAsked(await review.suggest(ws, item.pattern_id));
      void qc.invalidateQueries({ queryKey: ["ws", ws, "suggestions"] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  if (asked || s?.status === "generating") return <SuggestionState patternId={item.pattern_id} initial={asked ?? s} />;
  if (s?.status === "proposed")
    return (
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SuggestionLink s={s} />
        <Button size="sm" asChild>
          <HrefLink href={wsHref(ws, "suggestions", s.suggestion_id)} className="text-primary-foreground hover:no-underline">
            Review the fix
          </HrefLink>
        </Button>
      </div>
    );
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Note>{s?.status === "dismissed" ? "The last fix was turned down." : s?.status === "failed" ? "The last fix couldn't be written." : "No fix written yet."}</Note>
        <Button size="sm" onClick={() => void write()} disabled={busy} title="Write a change to the SOP from this verdict and its examples">
          <Sparkles /> {busy ? "Starting…" : "Write a fix"}
        </Button>
      </div>
      {error != null && <ErrorBanner error={error} />}
    </div>
  );
}

/** Fixing: the fix, and what it's waiting for. */
function FixingNext({ item }: { item: ReviewItem }) {
  const p = progressOf(item);
  const live = !!item.fix_release && !!p;
  return (
    <div className="grid gap-2">
      {item.suggestion && <SuggestionLink s={item.suggestion} />}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
        <span className="font-medium">{!item.fix_release ? "Waiting for a release" : live ? `In release ${item.fix_release}` : `In release ${item.fix_release}, not live yet`}</span>
        {p ? <Progress n={p.n} of={p.of} /> : item.review_note && <span className="text-muted-foreground">{item.review_note}</span>}
      </div>
    </div>
  );
}

function FixedNext({ item }: { item: ReviewItem }) {
  return (
    <div className="grid gap-1.5">
      <span className="text-ok inline-flex items-center gap-1.5 text-[13px] font-medium">
        <Check className="size-4" /> {item.fix_release ? `Fixed in release ${item.fix_release}.` : "Fixed."} <span className="text-muted-foreground font-normal">{item.review_note}</span>
      </span>
      {item.suggestion && <SuggestionLink s={item.suggestion} />}
    </div>
  );
}

function Progress({ n, of }: { n: number; of: number }) {
  return (
    <Hint focusable={false} tip={<TipBody head={`${n} of ${of} sessions`}>Sessions checked on the fix's release or later. At {of} with no findings in the pattern, it counts as fixed.</TipBody>}>
      <span className="text-muted-foreground inline-flex items-center gap-2 tabular-nums">
        <span className="bg-muted inline-block h-1.5 w-24 overflow-hidden rounded-full" aria-hidden>
          <span className="bg-primary block h-full rounded-full" style={{ width: `${Math.min(100, (100 * n) / of)}%` }} />
        </span>
        {n} of {of} sessions
      </span>
    </Hint>
  );
}

// --- a pattern's timeline ------------------------------------------------------------------------------------------

type Step = { key: string; label: ReactNode; detail?: ReactNode; at?: string | null; done: boolean; tone?: "ok" | "err" };

const APPLIED: Partial<Record<SuggestionSummary["status"], string>> = { applied: "Applied as a draft", pr_open: "Pull request opened", merged: "Pull request merged" };

/** Verdict → fix written → applied or PR → release → fixed or came back, from what the pattern page has. */
export function FixTimeline({ d }: { d: PatternDetail }) {
  const { ws, releases } = useWs();
  const v = d.verdict;
  const state = d.review_state;
  const fixes = d.suggestions.filter((s) => !v || s.created_at >= v.created_at);
  const applied = fixes.find((s) => s.status in APPLIED);
  const latest = applied ?? fixes[0];
  const rel = d.fix_release != null ? releases.find((r) => r.number === d.fix_release) : undefined;
  const p = progressOf(d);
  const pr = applied?.pr_url ? applied : undefined;

  const steps: Step[] = [
    {
      key: "verdict",
      label: v ? `Marked ${verdictPhrase(v.verdict)}` : "Waiting for a verdict",
      detail: v ? [v.reviewer, v.note && `“${v.note}”`].filter(Boolean).join(" · ") || undefined : undefined,
      at: v?.created_at,
      done: !!v,
    },
  ];
  if (state === "closed") {
    if (latest) steps.push({ key: "fix", label: "Exception written into the rule", detail: <SuggestionLinkInline s={latest} />, at: latest.created_at, done: latest.status in APPLIED });
    steps.push({ key: "closed", label: "Closed", detail: "Hidden from Insights rates. Nothing more to do.", done: true });
  } else if (v) {
    steps.push({ key: "fix", label: latest ? "Fix written" : "Fix not written yet", detail: latest ? <SuggestionLinkInline s={latest} /> : undefined, at: latest?.created_at, done: !!latest && latest.status !== "generating" && latest.status !== "failed" });
    steps.push({
      key: "applied",
      label: pr ? "Pull request opened" : applied ? APPLIED[applied.status] : "Not applied yet",
      detail: pr?.pr_url ? (
        <a href={pr.pr_url} target="_blank" rel="noreferrer" className="text-primary inline-flex items-center gap-1 hover:underline">
          Open the pull request <ExternalLink className="size-3" />
        </a>
      ) : undefined,
      at: pr ? pr.pr_opened_at : applied?.applied_at,
      done: !!applied,
    });
    if (pr)
      steps.push({
        key: "merged",
        label: pr.status === "merged" ? "Pull request merged" : "Waiting for the merge",
        detail: pr.status === "merged" && d.fix_release == null ? "Pull the repository into drafts (Settings), then publish." : undefined,
        at: pr.merged_at,
        done: pr.status === "merged",
      });
    steps.push({
      key: "release",
      label: d.fix_release != null ? `In release ${d.fix_release}` : "Waiting for a release",
      detail:
        d.fix_release != null ? (
          <>
            <HrefLink href={wsHref(ws, "releases", String(d.fix_release))}>See release {d.fix_release}</HrefLink>
            {rel?.current ? " · live now" : !p && state === "fixing" ? " · not live yet" : ""}
          </>
        ) : applied ? (
          d.review_note
        ) : undefined,
      at: applied?.released_at ?? rel?.created_at,
      done: d.fix_release != null,
    });
    steps.push(
      state === "fixed"
        ? { key: "end", label: "Fixed", detail: d.review_note, done: true, tone: "ok" }
        : state === "regressed"
          ? { key: "end", label: "Came back", detail: d.review_note, done: true, tone: "err" }
          : { key: "end", label: "Holding?", detail: p ? `${p.n} of ${p.of} sessions on the fix so far.` : "Needs 10 sessions on the fix's release with no findings.", done: false },
    );
  }

  return (
    <section className="bg-card rounded-lg border px-4 py-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[13.5px] font-semibold">Where it stands</h2>
        <ReviewStateChip state={state} note={d.review_note} />
      </div>
      {state && <p className="text-muted-foreground mb-3 text-[13px]">{state === "needs_review" && d.review_note ? d.review_note : REVIEW_STATE[state].help}</p>}
      <ol className="grid">
        {steps.map((s, i) => (
          <li key={s.key} className="relative grid grid-cols-[18px_minmax(0,1fr)] gap-x-2.5 pb-3 last:pb-0">
            {i < steps.length - 1 && <span className={cn("absolute top-[18px] bottom-0 left-[8.5px] w-px", s.done ? "bg-primary/40" : "bg-border")} aria-hidden />}
            <span
              className={cn(
                "mt-0.5 size-[18px] rounded-full border-2",
                s.tone === "ok" ? "border-ok bg-ok" : s.tone === "err" ? "border-err bg-err" : s.done ? "border-primary bg-primary" : "border-border bg-card",
              )}
              aria-hidden
            />
            <div className="min-w-0 text-[13px]">
              <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                <span className={cn("font-medium", !s.done && "text-muted-foreground", s.tone === "ok" && "text-ok", s.tone === "err" && "text-err")}>{s.label}</span>
                {s.at && (
                  <Hint focusable={false} tip={when(s.at)}>
                    <span className="text-muted-foreground text-xs">{ago(s.at)}</span>
                  </Hint>
                )}
              </div>
              {s.detail && <div className="text-muted-foreground mt-0.5">{s.detail}</div>}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function SuggestionLinkInline({ s }: { s: SuggestionSummary }) {
  const { ws } = useWs();
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      <SuggestionStatusTag status={s.status} />
      <ConflictTag freshness={s.freshness} />
      <HrefLink href={wsHref(ws, "suggestions", s.suggestion_id)} className="block min-w-0 flex-1 truncate" title={summaryOf(s) ?? undefined}>
        {summaryOf(s) ?? "The fix"}
      </HrefLink>
    </span>
  );
}
