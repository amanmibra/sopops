// Suggestions: SOP changes written from a pattern's verdict, by status, and one with its rationale, the source
// pattern, the block diff, how each affected agent's prompt changes, and apply / dismiss / regenerate.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, GitPullRequest, Loader2, Sparkles } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError, refresh } from "@/lib/api";
import { ruleName } from "@/lib/insights";
import { plural, when } from "@/lib/kinds";
import { type PlanAgent, SUGGESTION_STATUSES, type SuggestionDetail, type SuggestionStatus, review, rq, summaryOf } from "@/lib/review";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ConfirmButton, Counts, ErrorBanner, Note, PageTitle, Sub, Tag } from "./bits";
import { DiffView, PatchView } from "./diff-view";
import { author } from "./form";
import { HrefLink, useGo } from "./href-link";
import { repoQuery } from "./repo";
import { Skel } from "./insights/kit";
import { Page } from "./item-shell";
import { SUGGESTION_STATUS, SuggestionStatusTag, VerdictChip, ago, sessionAt } from "./review";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Collapsible } from "./views";

type Tab = SuggestionStatus | "all";

export function SuggestionsView({ status, onStatus }: { status?: Tab; onStatus: (s: Tab | undefined) => void }) {
  const { ws, cfg } = useWs();
  const tab: Tab = status ?? "proposed";
  const res = useQuery(rq.suggestions(ws, tab === "all" ? undefined : tab));
  const proposed = useQuery({ ...rq.suggestions(ws, "proposed"), retry: false });
  const items = res.data?.items ?? [];
  const procName = (id?: string | null) => (id ? (cfg.procedures.find((p) => p.id === id)?.name ?? id) : null);
  return (
    <Page wide>
      <PageTitle>Suggestions</PageTitle>
      <Sub className="max-w-[90ch]">
        SOP changes written from a pattern's verdict. Review the diff and how each agent's prompt changes, then apply it as a draft (or a pull request, with a connected repo) or dismiss it.
      </Sub>
      <nav className="mb-4 flex gap-0.5 overflow-x-auto border-b" aria-label="Suggestion status">
        {([...SUGGESTION_STATUSES, "all"] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            aria-current={tab === t ? "page" : undefined}
            onClick={() => onStatus(t === "proposed" ? undefined : t)}
            className={cn("text-muted-foreground hover:text-foreground -mb-px flex shrink-0 cursor-pointer items-center gap-1.5 border-b-2 border-transparent px-3 py-2", tab === t && "border-primary text-foreground font-medium")}
          >
            {t === "all" ? "All" : SUGGESTION_STATUS[t].label}
            {t === "proposed" && !!proposed.data?.items.length && <span className="bg-warn-soft text-warn rounded-full px-1.5 text-[11px] tabular-nums">{proposed.data.items.length}</span>}
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
        <div role="status" className="bg-card text-muted-foreground rounded-lg border px-4 py-10 text-center text-[13px]">
          <Sparkles className="text-muted-foreground mx-auto mb-1.5 size-5" />
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
                <SuggestionStatusTag status={s.status} />
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold">{summaryOf(s) ?? s.pattern_name ?? "Suggested change"}</span>
                  <span className="text-muted-foreground block text-xs">
                    {[procName(s.procedure_id), s.pattern_name && summaryOf(s) ? `from “${s.pattern_name}”` : null].filter(Boolean).join(" · ")}
                  </span>
                </span>
                {s.pr_url && <GitPullRequest className="text-primary size-4" aria-label="Has a pull request" />}
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
          <Tag tone="changed">Prompt changes</Tag>
          <b>{a.agent}</b>
          <Counts add={a.added} del={a.removed} />
          <span className="flex-1" />
          <HrefLink href={href("agent", a.agent, "prompt")} className="text-[13px]" onClick={(e) => e.stopPropagation()}>
            Full prompts
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
    <section className="bg-card rounded-lg border px-4 py-3.5">
      <div className="text-muted-foreground mb-1 text-xs">Source pattern</div>
      <h2 className="text-[15px] leading-snug font-semibold">
        <HrefLink href={wsHref(ws, "patterns", s.pattern_id)} className="text-foreground">
          {name ?? "…"}
        </HrefLink>
      </h2>
      {d && (
        <p className="text-muted-foreground mt-0.5 text-xs tabular-nums">
          {plural(d.sessions, "session", "sessions")} · {plural(d.findings, "finding", "findings")} in the last year
        </p>
      )}
      {(s.pattern?.description ?? d?.description) && <p className="text-muted-foreground mt-1 text-[13px]">{s.pattern?.description ?? d?.description}</p>}
      {d?.rule.text && (
        <blockquote className="border-k-procedure/50 mt-2 border-l-2 pl-2.5 text-[13px]">
          <span className="text-muted-foreground">{ruleName(d.rule.kind, d.rule.index)}:</span> {d.rule.text}
        </blockquote>
      )}
      <div className="mt-2.5 flex flex-wrap items-center gap-2 text-[13px]">
        <VerdictChip v={verdict} />
        {verdict?.reviewer && <span className="text-muted-foreground text-xs">by {verdict.reviewer}</span>}
      </div>
      {verdict?.note && <p className="text-muted-foreground mt-1 text-[13px]">“{verdict.note}”</p>}
      {d && d.examples.length > 0 && (
        <>
          <h3 className="text-muted-foreground mt-3 mb-1 text-xs font-semibold">Examples</h3>
          <ul className="grid grid-cols-[minmax(0,1fr)] gap-1.5">
            {d.examples.slice(0, 3).map((e, i) => (
              <li key={i} className="text-[13px]">
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

export function SuggestionView({ id }: { id: string }) {
  const { ws, cfg, href } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const repo = useQuery({ ...repoQuery(ws), retry: false });
  const res = useQuery({ ...rq.suggestion(ws, id), refetchInterval: (query) => (query.state.data?.status === "generating" ? 2500 : false) });
  const [busy, setBusy] = useState<null | "apply" | "dismiss" | "regenerate">(null);
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

  const act = async (what: "apply" | "dismiss" | "regenerate") => {
    setBusy(what);
    setError(null);
    try {
      if (what === "apply") {
        const r = await review.apply(ws, id, author());
        toast.success(r.status === "pr_open" ? "Pull request opened." : "Saved as a draft. Publish from Unpublished changes to send it to agents.");
      } else if (what === "dismiss") {
        await review.dismiss(ws, id, note.trim() || null, author());
        toast.success("Dismissed.");
        setDismissing(false);
      } else {
        const r = await review.suggest(ws, s.pattern_id);
        toast.success("A new suggestion is being written.");
        await refresh(qc, ws);
        if (r.suggestion_id !== id) return void go(wsHref(ws, "suggestions", r.suggestion_id), { replace: true });
      }
      await refresh(qc, ws);
    } catch (e) {
      setError(what === "apply" && e instanceof ApiError && e.status === 409 ? new Error(`${e.message} If the procedure changed since this suggestion was written, regenerate it to get one against the current text.`) : e);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Page wide>
      <div className="text-muted-foreground mb-2 text-[13px]">
        <HrefLink href={wsHref(ws, "suggestions")}>Suggestions</HrefLink> /
      </div>
      <PageTitle aside={<SuggestionStatusTag status={s.status} big />}>{patternName ? `Fix for “${patternName}”` : "Suggested change"}</PageTitle>
      <Sub>
        An edit to procedure <HrefLink href={href("procedure", s.block_id ?? s.procedure_id ?? undefined)}>{procName}</HrefLink>, written {when(s.created_at)}
        {agents.length > 0 && `, changing ${plural(agents.length, "agent's prompt", "agents' prompts")}`}
        {s.attempts > 1 && ` (took ${s.attempts} tries to compile)`}.
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
          Applied as a draft edit{s.reviewer ? ` by ${s.reviewer}` : ""}. <HrefLink href={wsHref(ws, "changes")}>Review and publish it in Unpublished changes</HrefLink>.
        </Banner>
      )}
      {s.status === "pr_open" && s.pr_url && (
        <Banner tone="ok">
          Pull request open:{" "}
          <a href={s.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1">
            {s.pr_url.replace(/^https?:\/\/(www\.)?github\.com\//, "")} <ExternalLink className="size-3.5" />
          </a>
        </Banner>
      )}
      {s.status === "dismissed" && (
        <Banner>
          Dismissed{s.reviewer ? ` by ${s.reviewer}` : ""}
          {s.note ? `: “${s.note}”` : "."} Regenerate to get a new one.
        </Banner>
      )}
      {error != null && <ErrorBanner error={error} lead="This couldn't be done:" />}

      {s.status !== "generating" && s.status !== "applied" && s.status !== "pr_open" && (
        <div className="bg-card mb-4 flex flex-wrap items-center gap-2 rounded-lg border px-4 py-3">
          {open && (
            <Button disabled={!!busy || !s.new_text} onClick={() => void act("apply")}>
              {busy === "apply" ? <Loader2 className="animate-spin" /> : pr ? <GitPullRequest /> : null}
              {pr ? "Open PR" : "Save as draft"}
            </Button>
          )}
          {open &&
            (dismissing ? (
              <span className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
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
          <Button variant="ghost" disabled={!!busy} onClick={() => void act("regenerate")}>
            {busy === "regenerate" ? <Loader2 className="animate-spin" /> : <Sparkles />} Regenerate
          </Button>
          {open && <Note className="basis-full sm:ml-auto sm:basis-auto">{pr ? `Opens a pull request on ${repo.data!.owner}/${repo.data!.repo}.` : "No repository connected: it lands as an unpublished draft."}</Note>}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4">
          {s.rationale && (
            <section className="bg-card rounded-lg border px-4 py-3.5">
              <h2 className="mb-1.5 text-[13.5px] font-semibold">Why</h2>
              <p className="max-w-[80ch] text-[14px] whitespace-pre-wrap">{s.rationale}</p>
            </section>
          )}
          <section className="bg-card min-w-0 rounded-lg border px-4 py-3.5">
            <h2 className="text-[13.5px] font-semibold">
              The change to {procName}
              {s.path && <span className="text-muted-foreground ml-2 font-mono text-xs font-normal">{s.path}</span>}
            </h2>
            {s.new_text != null ? <DiffView before={s.old_text} after={s.new_text} /> : <Note>{s.status === "failed" ? "No edit was proposed." : "Not written yet."}</Note>}
          </section>
          {s.new_text != null && (
            <section className="min-w-0">
              <h2 className="mb-2.5 text-base font-semibold">{agents.length ? `How ${agents.length === 1 ? "the agent's prompt changes" : "agents' prompts change"}` : "No agent's prompt changes"}</h2>
              {agents.length ? agents.map((a, i) => <AgentChange key={a.agent} a={a} open={i < 2} />) : <Note>The edit doesn't reach any agent's prompt as it stands.</Note>}
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
