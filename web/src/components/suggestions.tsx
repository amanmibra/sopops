// Suggestions: SOP changes written from a pattern's verdict, by status, and one with its rationale, the source
// pattern, the block diff, how each affected agent's instructions change, and apply / dismiss / regenerate. A proposed one
// says how it fits the procedure as it is now: unchanged, changed but merging cleanly, or in conflict (then it can be
// rewritten against the current text, as a new suggestion).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, GitMerge, GitPullRequest, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError, refresh } from "@/lib/api";
import { ruleName } from "@/lib/insights";
import { plural, when } from "@/lib/kinds";
import { type MergeConflict, type PlanAgent, type SinceWritten, type SuggestionDetail, type SuggestionStatus, review, rq, summaryOf } from "@/lib/review";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ConfirmButton, Counts, ErrorBanner, Note, PageTitle, RuleText, Sub, Tag } from "./bits";
import { GitHubIcon } from "./brand-icon";
import { DiffView, PatchView } from "./diff-view";
import { author } from "./form";
import { HrefLink, useGo } from "./href-link";
import { repoQuery } from "./repo";
import { Skel } from "./insights/kit";
import { Page } from "./item-shell";
import { sessionAt } from "./review";
import { ConflictTag, FixAsk, ReviewStateChip, SUGGESTION_STATUS, SuggestionStatusTag, VerdictChip, ago } from "./review-bits";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Collapsible } from "./views";

type Tab = SuggestionStatus | "all";
/** The tabs, and the statuses each lists: a pull request waits under Proposed, and a merged one counts as Applied. */
export const FIX_TABS: { tab: Tab; statuses: SuggestionStatus[] | null }[] = [
  { tab: "proposed", statuses: ["proposed", "pr_open"] },
  { tab: "applied", statuses: ["applied", "merged"] },
  { tab: "dismissed", statuses: ["dismissed"] },
  { tab: "failed", statuses: ["failed"] },
  { tab: "all", statuses: null },
];
/** An old link's status, as the tab that now lists it. */
export const tabOf = (s: SuggestionStatus | "all"): Tab => (s === "pr_open" ? "proposed" : s === "merged" ? "applied" : s);

export function SuggestionsView({ status, onStatus }: { status?: Tab; onStatus: (s: Tab | undefined) => void }) {
  const { ws, cfg } = useWs();
  const tab: Tab = status ? tabOf(status) : "proposed";
  const res = useQuery(rq.suggestions(ws));
  const inTab = (t: Tab) => (res.data?.items ?? []).filter((x) => FIX_TABS.find((f) => f.tab === t)?.statuses?.includes(x.status) ?? true);
  const items = inTab(tab);
  const waiting = inTab("proposed").length;
  const procName = (id?: string | null) => (id ? (cfg.procedures.find((p) => p.id === id)?.name ?? id) : null);
  return (
    <Page wide>
      <PageTitle>Fixes</PageTitle>
      <Sub className="max-w-[90ch]">
        SOP changes written from a pattern's verdict. Review the diff and how each agent's instructions change, then apply it as a draft (or a pull request, with a connected repo) or dismiss it.
      </Sub>
      <nav className="mb-4 flex gap-0.5 overflow-x-auto border-b" aria-label="Suggestion status">
        {FIX_TABS.map(({ tab: t }) => (
          <button
            key={t}
            type="button"
            aria-current={tab === t ? "page" : undefined}
            onClick={() => onStatus(t === "proposed" ? undefined : t)}
            className={cn("text-muted-foreground hover:text-foreground -mb-px flex shrink-0 cursor-pointer items-center gap-2 border-b-2 border-transparent px-3 py-2", tab === t && "border-primary text-foreground font-medium")}
          >
            {t === "all" ? "All" : SUGGESTION_STATUS[t].label}
            {t === "proposed" && !!waiting && <span className="bg-warn-soft text-warn rounded-full px-2 text-sm tabular-nums">{waiting}</span>}
          </button>
        ))}
      </nav>
      {res.isPending ? (
        <div className="bg-card grid gap-2 rounded-lg border p-4" aria-busy>
          {[0, 1, 2].map((i) => (
            <Skel key={i} className="h-10" />
          ))}
        </div>
      ) : res.isError ? (
        <Banner tone="err">
          Couldn't load suggestions. {res.error.message}{" "}
          <Button size="xs" variant="outline" onClick={() => void res.refetch()}>
            Try again
          </Button>
        </Banner>
      ) : !items.length ? (
        <div role="status" className="bg-card text-muted-foreground rounded-lg border px-4 py-10 text-center text-sm">
          <Sparkles className="text-muted-foreground mx-auto mb-2 size-5" />
          <div className="text-foreground font-semibold">{tab === "proposed" ? "No suggestions waiting" : `Nothing ${tab === "all" ? "yet" : SUGGESTION_STATUS[tab].label.toLowerCase()}`}</div>
          <p className="mx-auto mt-1 max-w-[60ch]">
            A verdict of “real mistake” or “SOP is wrong” on a pattern gets a suggested fix. <HrefLink href={wsHref(ws, "patterns")}>Review patterns</HrefLink>
          </p>
        </div>
      ) : (
        <ul className="bg-card divide-y rounded-lg border">
          {items.map((s) => (
            <li key={s.suggestion_id}>
              <HrefLink href={wsHref(ws, "suggestions", s.suggestion_id)} className="text-foreground hover:bg-muted/60 flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 hover:no-underline">
                <span className="flex items-center gap-1.5">
                  {s.status === "pr_open" && <GitPullRequest className="text-primary size-4" aria-hidden />}
                  {s.status === "merged" && <GitMerge className="text-primary size-4" aria-hidden />}
                  <SuggestionStatusTag status={s.status} />
                </span>
                <ConflictTag freshness={s.freshness} />
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold">{summaryOf(s) ?? s.pattern_name ?? "Suggested change"}</span>
                  <span className="text-muted-foreground block text-xs">
                    {[procName(s.procedure_id), s.pattern_name && summaryOf(s) ? `from “${s.pattern_name}”` : null].filter(Boolean).join(" · ")}
                  </span>
                </span>
                <span className="text-muted-foreground text-xs whitespace-nowrap">{ago(s.created_at)}</span>
              </HrefLink>
            </li>
          ))}
        </ul>
      )}
    </Page>
  );
}

function AgentChange({ a, open }: { a: PlanAgent; open: boolean }) {
  const { href } = useWs();
  return (
    <Collapsible
      open={open}
      summary={
        <>
          <Tag tone="changed">Instructions change</Tag>
          <b>{a.agent}</b>
          <Counts add={a.added} del={a.removed} />
          <span className="flex-1" />
          <HrefLink href={href("agent", a.agent, "prompt")} className="text-sm" onClick={(e) => e.stopPropagation()}>
            Full instructions
          </HrefLink>
        </>
      }
    >
      <PatchView patch={a.diff} />
    </Collapsible>
  );
}

function SourcePattern({ s }: { s: SuggestionDetail }) {
  const { ws } = useWs();
  const pat = useQuery({ ...rq.pattern(ws, s.pattern_id), retry: false });
  const d = pat.data;
  const name = s.pattern?.name ?? s.pattern_name ?? d?.name;
  const verdict = s.verdict ?? d?.verdict ?? null;
  const procedureId = s.pattern?.procedure_id ?? s.procedure_id ?? d?.procedure_id ?? "";
  const ruleId = s.pattern?.rule_id ?? s.rule_id ?? d?.rule_id ?? "";
  if (!name && pat.isError) return <Note>The source pattern is gone (reclustered away).</Note>;
  return (
    <section className="bg-card rounded-lg border px-4 py-4">
      <div className="text-muted-foreground mb-1 text-xs">Source pattern</div>
      <h2 className="text-lg font-semibold">
        <HrefLink href={wsHref(ws, "patterns", s.pattern_id)} className="text-foreground">
          {name ?? "…"}
        </HrefLink>
      </h2>
      {d && (
        <p className="text-muted-foreground mt-0.5 text-xs tabular-nums">
          {plural(d.session_ids.length, "session", "sessions")} · {plural(d.findings + d.suppressed, "finding", "findings")} in the last year
          {d.suppressed > 0 && ` (${d.suppressed} hidden from Insights by the verdict)`}
        </p>
      )}
      {(s.pattern?.kind ?? d?.kind) !== "rule" && (s.pattern?.description ?? d?.description) && <p className="text-muted-foreground mt-1 text-sm">{s.pattern?.description ?? d?.description}</p>}
      {d?.rule.text && (
        <blockquote className="border-k-procedure/50 mt-2 border-l-2 pl-2 text-sm">
          <span className="text-muted-foreground">{ruleName(d.rule.kind, d.rule.index)}:</span> <RuleText text={d.rule.text} />
        </blockquote>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
        <VerdictChip v={verdict} />
        {d && <ReviewStateChip state={d.review_state} note={d.review_note} />}
        {verdict?.reviewer && <span className="text-muted-foreground text-xs">by {verdict.reviewer}</span>}
      </div>
      {verdict?.note && <p className="text-muted-foreground mt-1 text-sm">“{verdict.note}”</p>}
      {d && d.review_state && ["fixing", "fixed", "regressed"].includes(d.review_state) && d.review_note && <p className="mt-1 text-sm">{d.review_note}</p>}
      {d && d.examples.length > 0 && (
        <>
          <h3 className="text-muted-foreground mt-3 mb-1 text-xs font-semibold">Examples</h3>
          <ul className="grid grid-cols-[minmax(0,1fr)] gap-2">
            {d.examples.slice(0, 3).map((e, i) => (
              <li key={i} className="text-sm">
                <HrefLink href={sessionAt(ws, { session_id: e.session_id, turn: e.turn }, procedureId, ruleId)} className="text-foreground hover:bg-muted block rounded-md border px-2 py-1 hover:no-underline">
                  “{e.quote}”
                </HrefLink>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function ChangesSince({ since, open = false }: { since: SinceWritten; open?: boolean }) {
  if (!since.changes) return null;
  return (
    <Collapsible open={open} summary={<span className="text-sm font-medium">What changed in the procedure since</span>}>
      <PatchView patch={since.changes} />
    </Collapsible>
  );
}

function Side({ label, text }: { label: string; text: string }) {
  return (
    <div className="min-w-0">
      <div className="text-muted-foreground mb-1 text-xs font-medium">{label}</div>
      <pre className="bg-card overflow-x-auto rounded-md border px-2 py-2 font-mono text-xs whitespace-pre-wrap">{text.replace(/^\n+|\n+$/g, "") || <span className="text-muted-foreground italic">Nothing</span>}</pre>
    </div>
  );
}

function ConflictPart({ c }: { c: MergeConflict }) {
  return (
    <div className="grid gap-2">
      <div className="text-xs font-semibold">At line {c.line}</div>
      <div className="grid gap-2 md:grid-cols-2">
        <Side label="The procedure now" text={c.current} />
        <Side label="This suggestion" text={c.suggested} />
      </div>
    </div>
  );
}

/** A proposed suggestion whose procedure changed since it was written, in the same place (or the merge doesn't compile). */
function ConflictCallout({ since, busy, onRewrite }: { since: SinceWritten; busy: boolean; onRewrite: () => void }) {
  return (
    <section role="alert" className="border-err/40 bg-err-soft/60 mb-4 grid gap-3 rounded-lg border px-4 py-4">
      <div>
        <h2 className="text-err text-sm font-semibold">This conflicts with changes made since it was written</h2>
        <p className="mt-0.5 text-sm">
          {since.conflicts.length
            ? `Someone changed the same ${since.conflicts.length === 1 ? "part" : "parts"} of the procedure. Applying it would undo their edit, so it can't be applied.`
            : since.issues && since.changes
              ? "The edits don't overlap, but together the file doesn't compile. It can't be applied."
              : "It can't be applied."}{" "}
          Rewrite it against the current text to keep both.
        </p>
      </div>
      {since.conflicts.map((c, i) => (
        <ConflictPart key={i} c={c} />
      ))}
      {since.issues && <pre className="bg-card overflow-x-auto rounded-md border px-2 py-2 font-mono text-xs whitespace-pre-wrap">{since.issues}</pre>}
      <div className="flex flex-wrap items-center gap-2">
        <Button disabled={busy} onClick={onRewrite}>
          {busy ? <Loader2 className="animate-spin" /> : <RefreshCw />} Rewrite against the current text
        </Button>
        <Note>A new suggestion, written from the procedure as it is now. This one is dismissed.</Note>
      </div>
    </section>
  );
}

export function SuggestionView({ id }: { id: string }) {
  const { ws, cfg, href } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const repo = useQuery({ ...repoQuery(ws), retry: false });
  const res = useQuery({ ...rq.suggestion(ws, id), refetchInterval: (query) => (query.state.data?.status === "generating" ? 2500 : false) });
  const [busy, setBusy] = useState<null | "apply" | "dismiss" | "regenerate" | "rewrite">(null);
  const [note, setNote] = useState("");
  const [dismissing, setDismissing] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (res.isPending)
    return (
      <Page wide>
        <Skel className="mb-2 h-4 w-40" />
        <Skel className="mb-4 h-7 w-96 max-w-full" />
        <Skel className="h-60" />
      </Page>
    );
  if (res.isError)
    return (
      <Page>
        <ErrorBanner error={res.error} />
        <HrefLink href={wsHref(ws, "suggestions")}>All suggestions</HrefLink>
      </Page>
    );
  const s = res.data;
  const pr = !!repo.data;
  const open = s.status === "proposed";
  const procName = cfg.procedures.find((x) => x.id === s.block_id || x.id === s.procedure_id)?.name ?? s.procedure_id;
  const agents = s.plan?.agents ?? [];
  const patternName = s.pattern?.name ?? s.pattern_name;
  const since = open ? s.since_written : null;
  const conflict = since?.state === "conflict";

  const [asking, setAsking] = useState<null | "regenerate" | "rewrite">(null); // what the next one should do, asked first
  const act = async (what: "apply" | "dismiss" | "regenerate" | "rewrite", guidance: string | null = null) => {
    setAsking(null);
    setBusy(what);
    setError(null);
    try {
      if (what === "apply") {
        const r = await review.apply(ws, id, author());
        const merged = r.merged_into_version != null ? " Merged with the changes made since." : "";
        toast.success(r.status === "pr_open" ? `Pull request opened.${merged}` : `Saved as a draft.${merged} Publish from Unpublished changes to send it to agents.`);
      } else if (what === "rewrite") {
        const r = await review.rewrite(ws, id, author(), guidance);
        toast.success("Rewriting it against the current text.");
        await refresh(qc, ws);
        return void go(wsHref(ws, "suggestions", r.suggestion_id), { replace: true });
      } else if (what === "dismiss") {
        await review.dismiss(ws, id, note.trim() || null, author());
        toast.success("Dismissed.");
        setDismissing(false);
      } else {
        const r = await review.suggest(ws, s.pattern_id, guidance);
        toast.success("A new suggestion is being written.");
        await refresh(qc, ws);
        if (r.suggestion_id !== id) return void go(wsHref(ws, "suggestions", r.suggestion_id), { replace: true });
      }
      await refresh(qc, ws);
    } catch (e) {
      const stale = what === "apply" && e instanceof ApiError && e.status === 409;
      setError(stale && !/rewrite/i.test(e.message) ? new Error(`${e.message} If the procedure changed since this suggestion was written, rewrite it against the current text.`) : e);
      if (stale) await refresh(qc, ws);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Page wide>
      <div className="text-muted-foreground mb-2 text-sm">
        <HrefLink href={wsHref(ws, "suggestions")}>Fixes</HrefLink> /
      </div>
      <PageTitle aside={<SuggestionStatusTag status={s.status} big />}>{patternName ? `Fix for “${patternName}”` : "Suggested change"}</PageTitle>
      <Sub>
        An edit to procedure <HrefLink href={href("procedure", s.block_id ?? s.procedure_id ?? undefined)}>{procName}</HrefLink>, written {when(s.created_at)}
        {agents.length > 0 && `, changing ${plural(agents.length, "agent's instructions", "agents' instructions")}`}
        {s.attempts > 1 && ` (took ${s.attempts} tries to compile)`}.
        {s.rewrite_of && (
          <>
            {" "}
            It replaces <HrefLink href={wsHref(ws, "suggestions", s.rewrite_of)}>an earlier suggestion</HrefLink> that conflicted with later edits.
          </>
        )}
      </Sub>

      {s.status === "generating" && (
        <Banner>
          <span className="inline-flex items-center gap-2">
            <Loader2 className="size-4 animate-spin" /> Being written and checked with sopc. This page updates when it's ready.
          </span>
        </Banner>
      )}
      {s.status === "failed" && <Banner tone="err">This suggestion couldn't be made{s.error ? `: ${s.error}` : "."} Regenerate to try again.</Banner>}
      {s.status === "applied" && (
        <Banner tone="ok">
          Applied as a draft edit{s.reviewer ? ` by ${s.reviewer}` : ""}.{s.merged_into_version != null && " Merged with changes made since it was written."}{" "}
          <HrefLink href={wsHref(ws, "changes")}>Review and publish it in Unpublished changes</HrefLink>.
        </Banner>
      )}
      {s.status === "pr_open" && s.pr_url && (
        <Banner tone="ok">
          Pull request open:{" "}
          <a href={s.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1">
            <GitHubIcon size={14} /> {s.pr_url.replace(/^https?:\/\/(www\.)?github\.com\//, "")} <ExternalLink className="size-3.5" />
          </a>
          {s.merged_into_version != null && " Merged with changes made since it was written."}
        </Banner>
      )}
      {s.status === "dismissed" &&
        (s.rewritten_as ? (
          <Banner>
            Rewritten against the current text{s.reviewer ? ` by ${s.reviewer}` : ""}. <HrefLink href={wsHref(ws, "suggestions", s.rewritten_as)}>Open the new suggestion</HrefLink>.
          </Banner>
        ) : (
          <Banner>
            Dismissed{s.reviewer ? ` by ${s.reviewer}` : ""}
            {s.note ? `: “${s.note}”` : "."} Regenerate to get a new one.
          </Banner>
        ))}
      {since && conflict && <ConflictCallout since={since} busy={!!busy || !!asking} onRewrite={() => setAsking("rewrite")} />}
      {asking && (
        <div className="bg-card mb-4 rounded-lg border px-4 py-3">
          <FixAsk
            key={asking}
            initial={s.guidance}
            busy={!!busy}
            label={asking === "rewrite" ? "Rewrite it" : "Write a new one"}
            onWrite={(g) => void act(asking, g)}
            onCancel={() => setAsking(null)}
          />
          {asking === "rewrite" && s.guidance && <Note className="mt-2 block">Left as it is, the rewrite keeps what this one was asked to do.</Note>}
        </div>
      )}
      {error != null && <ErrorBanner error={error} lead="This couldn't be done:" />}

      {s.status !== "generating" && s.status !== "applied" && s.status !== "pr_open" && (
        <div className="bg-card mb-4 flex flex-wrap items-center gap-2 rounded-lg border px-4 py-3">
          {open && (
            <Button variant={conflict ? "outline" : "default"} disabled={!!busy || !s.new_text || conflict} title={conflict ? "It conflicts with changes made since. Rewrite it first." : undefined} onClick={() => void act("apply")}>
              {busy === "apply" ? <Loader2 className="animate-spin" /> : pr ? <GitPullRequest /> : null}
              {pr ? "Open PR" : "Save as draft"}
            </Button>
          )}
          {open &&
            (dismissing ? (
              <span className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                <Input autoFocus className="h-8 min-w-48 flex-1" placeholder="Why not? (optional)" aria-label="Why it's dismissed" value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setDismissing(false)} />
                <ConfirmButton size="sm" variant="outline" confirm="Really dismiss?" disabled={!!busy} onConfirm={() => void act("dismiss")}>
                  Dismiss
                </ConfirmButton>
                <Button size="sm" variant="ghost" onClick={() => setDismissing(false)}>
                  Cancel
                </Button>
              </span>
            ) : (
              <Button variant="outline" disabled={!!busy} onClick={() => setDismissing(true)}>
                Dismiss…
              </Button>
            ))}
          {!conflict && (
            <Button variant="ghost" disabled={!!busy || !!asking} onClick={() => setAsking("regenerate")}>
              {busy === "regenerate" ? <Loader2 className="animate-spin" /> : <Sparkles />} Regenerate
            </Button>
          )}
          {open && !conflict && <Note className="basis-full sm:ml-auto sm:basis-auto">{pr ? `Opens a pull request on ${repo.data!.owner}/${repo.data!.repo}.` : "No repository connected: it lands as an unpublished draft."}</Note>}
          {since?.state === "merges" && (
            <p className="text-muted-foreground flex basis-full items-center gap-2 text-sm">
              <GitMerge className="size-4 shrink-0" /> The procedure changed since this was written. It merges cleanly.
            </p>
          )}
        </div>
      )}
      {since && since.state !== "current" && <ChangesSince since={since} open={conflict && !since.conflicts.length} />}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4">
          {(s.rationale || s.guidance) && (
            <section className="bg-card rounded-lg border px-4 py-4">
              <h2 className="mb-2 text-sm font-semibold">Why</h2>
              {s.guidance && (
                <p className="text-muted-foreground mb-2 max-w-[80ch] text-sm whitespace-pre-wrap">
                  <span className="text-foreground font-medium">Asked for:</span> “{s.guidance}”
                </p>
              )}
              {s.rationale && <p className="max-w-[80ch] text-sm whitespace-pre-wrap">{s.rationale}</p>}
            </section>
          )}
          <section className="bg-card min-w-0 rounded-lg border px-4 py-4">
            <h2 className="text-sm font-semibold">
              The change to {procName}
              {s.path && <span className="text-muted-foreground ml-2 font-mono text-xs font-normal">{s.path}</span>}
            </h2>
            {s.new_text != null ? <DiffView before={s.old_text} after={s.new_text} /> : <Note>{s.status === "failed" ? "No edit was proposed." : "Not written yet."}</Note>}
          </section>
          {s.new_text != null && (
            <section className="min-w-0">
              <h2 className="mb-2 text-base font-semibold">{agents.length ? `How ${agents.length === 1 ? "the agent's instructions change" : "agents' instructions change"}` : "No agent's instructions change"}</h2>
              {agents.length ? agents.map((a, i) => <AgentChange key={a.agent} a={a} open={i < 2} />) : <Note>The edit doesn't reach any agent's instructions as it stands.</Note>}
            </section>
          )}
        </div>
        <aside className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4">
          <SourcePattern s={s} />
        </aside>
      </div>
    </Page>
  );
}
