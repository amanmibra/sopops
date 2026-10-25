// Home: what needs you (mistakes to review, fixes waiting, unpublished changes) beside what the published fixes did,
// then this week's miss rate against the week before, and the latest sessions. Every figure comes from the API; the
// only arithmetic is a before/after difference and the sessions it adds up to.
import { useInfiniteQuery, useQueries, useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { q } from "@/lib/api";
import { fmtPct } from "@/lib/insights";
import { KIND, when } from "@/lib/kinds";
import { rq } from "@/lib/review";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Note, PageTitle, Sub, Tag } from "./bits";
import { ResultTag } from "./checks";
import { HrefLink } from "./href-link";
import { OutcomeBar } from "./insights/kit";
import { Page } from "./item-shell";
import { useNeedsReview } from "./review";

const SHOWN = 2;

/** One queue that waits on a person: its count, its first few items, and the page that works through it. */
function Queue({ label, n, items, href, action, empty }: { label: string; n: number | null; items: { key: string; href: string; text: string }[]; href: string; action: string; empty: string }) {
  return (
    <section className="grid min-w-0 grid-cols-[48px_minmax(0,1fr)] gap-x-4 border-t px-4 py-4 first:border-t-0 md:border-t-0 md:border-l md:[&:nth-child(2)]:border-l-0">
      <div className={cn("t-head", !n && "text-muted-foreground")}>{n ?? "—"}</div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
          <h3 className="text-muted-foreground text-sm">{label}</h3>
          <HrefLink href={href} className="t-label text-foreground">
            {action} →
          </HrefLink>
        </div>
        <ul className="mt-2 grid gap-2">
          {n ? (
            items.slice(0, SHOWN).map((it) => (
              <li key={it.key} className="truncate">
                <HrefLink href={it.href} className="text-foreground">
                  {it.text}
                </HrefLink>
              </li>
            ))
          ) : (
            <li className="text-muted-foreground">{empty}</li>
          )}
          {n != null && n > SHOWN && <li className="text-muted-foreground">and {n - SHOWN} more</li>}
        </ul>
      </div>
    </section>
  );
}

function Block({ title, aside, children, className }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("bg-card min-w-0 border", className)}>
      <header className="flex items-baseline justify-between gap-4 border-b bg-[var(--band)] px-4 py-2">
        <h2 className="text-muted-foreground text-sm">{title}</h2>
        {aside}
      </header>
      {children}
    </section>
  );
}

const WEEK = 7 * 24 * 3600 * 1000;
const inWeek = (t?: string | null) => !!t && Date.now() - new Date(t).getTime() < WEEK;

type Rate = { n: number; pass_rate: number | null; thin: boolean };
type FixRow = {
  key: string;
  name: string;
  href: string;
  state: "fixing" | "fixed" | "regressed";
  release: number | null;
  sessions: number;
  needed: number;
  note: string | null;
  /** Its rule (by lineage) on the release before the fix and on the fix's release, from Insights' release compare. */
  before?: Rate;
  after?: Rate;
  /** Points of miss rate the fix took away (negative: worse); null when either side is thin. */
  drop: number | null;
};

const miss = (r: Rate) => 100 - (r.pass_rate ?? 0);

/**
 * What the fixes did, built from the mistakes (rule patterns) in a fix's states: fixing, fixed, regressed. Each carries
 * its fix's release and the sessions checked on it since. The before and after rates come from Insights' release
 * compare (the fix's release against the one before), matched on the rule's lineage id, the pattern's own rule_id; the
 * delta shows only when neither side is thin (n under Insights' minimum).
 */
function Fixed() {
  const { ws, legacy } = useWs();
  const fixes = useQuery({ ...rq.review(ws, ["fixing", "fixed", "regressed"]), retry: false, enabled: !legacy }).data?.items;
  const all = useQuery({ ...rq.suggestions(ws), retry: false, enabled: !legacy }).data?.items;
  const patterns = useQuery({ ...q.patterns(ws), retry: false, enabled: !legacy }).data?.items;
  const releases = [...new Set((fixes ?? []).map((f) => f.fix_release).filter((r): r is number => r != null && r > 1))];
  const compares = useQueries({ queries: releases.map((r) => ({ ...q.insightsCompare(ws, { window: "365d" }, r - 1, r), retry: false })) });

  const rows: FixRow[] = (fixes ?? []).map((p) => {
    const R = p.fix_release;
    const cmp = R != null ? compares[releases.indexOf(R)]?.data : undefined;
    const rule = cmp?.rules.find((r) => r.procedure_id === p.procedure_id && r.lineage_id === p.rule_id);
    const before = rule?.a ? { n: rule.a.n, pass_rate: rule.a.pass_rate, thin: rule.a.thin } : undefined;
    const after = rule?.b ? { n: rule.b.n, pass_rate: rule.b.pass_rate, thin: rule.b.thin } : undefined;
    const ok = before && after && !before.thin && !after.thin && before.pass_rate != null && after.pass_rate != null;
    return {
      key: p.pattern_id,
      name: p.name,
      href: wsHref(ws, "patterns", p.pattern_id),
      state: p.review_state as FixRow["state"],
      release: R,
      sessions: p.fix_sessions ?? 0,
      needed: p.fix_sessions_needed ?? 10,
      note: p.review_note,
      before,
      after,
      drop: ok ? miss(before!) - miss(after!) : null,
    };
  });
  // Wins first (by the biggest drop, then the rest that held), then what came back, then what's too early.
  const order = (r: FixRow) => (r.state === "fixed" ? 0 : r.state === "regressed" ? 1 : 2);
  rows.sort((a, b) => order(a) - order(b) || (b.drop ?? -1) - (a.drop ?? -1) || (b.release ?? 0) - (a.release ?? 0));

  const verdicts = new Set<string>();
  for (const p of patterns ?? [])
    for (const x of [p, ...p.sub_patterns]) if (x.verdict && !x.verdict.inherited && inWeek(x.verdict.created_at)) verdicts.add(x.verdict.verdict_id);
  const approved = (all ?? []).filter((f) => inWeek(f.applied_at)).length;
  const published = (all ?? []).filter((f) => inWeek(f.released_at)).length;
  const loading = !fixes || compares.some((c) => c.isPending);

  return (
    <Block title="What the fixes did" aside={<HrefLink href={wsHref(ws, "results")} className="t-label text-foreground">Results →</HrefLink>} >
      <dl className="grid grid-cols-3 border-b">
        {(
          [
            ["Mistakes closed", patterns ? verdicts.size : null],
            ["Fixes approved", all ? approved : null],
            ["Fixes published", all ? published : null],
          ] as const
        ).map(([k, v]) => (
          <div key={k} className="min-w-0 border-l px-4 py-2 first:border-l-0">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="t-lead tabular-nums">{v ?? "—"}</dd>
          </div>
        ))}
      </dl>
      <p className="text-muted-foreground border-b px-4 py-2">This week. Below, each fixed mistake and what its rule did on the fix's release.</p>
      {loading ? (
        <Note className="block px-4 py-4">Loading…</Note>
      ) : !rows.length ? (
        <p className="px-4 py-4">
          No fix has been applied yet. <HrefLink href={wsHref(ws, "suggestions")}>Approve a fix</HrefLink> and its effect shows up here.
        </p>
      ) : (
        <>
          <FixTable rows={rows.slice(0, SHOWN_FIXES)} />
          <ul className="md:hidden">
            {rows.slice(0, SHOWN_FIXES).map((r) => (
              <FixLine key={r.key} r={r} />
            ))}
          </ul>
          {rows.length > SHOWN_FIXES && (
            <p className="border-t px-4 py-2">
              <HrefLink href={wsHref(ws, "patterns") + "?tab=fixing"} className="text-muted-foreground">
                {rows.length - SHOWN_FIXES} more in Mistakes
              </HrefLink>
            </p>
          )}
        </>
      )}
    </Block>
  );
}

const SHOWN_FIXES = 5;

const STATUS: Record<FixRow["state"], { label: string; tone: string }> = {
  fixed: { label: "Held", tone: "live" },
  regressed: { label: "Came back", tone: "removed" },
  fixing: { label: "Too early", tone: "" },
};

/** What one fix's row says in words: why it holds, came back, or can't be told yet. */
function fixText(r: FixRow): string {
  const rel = r.release != null ? `release #${r.release}` : null;
  if (r.state === "fixing") return rel ? `${r.sessions} of ${r.needed} sessions on ${rel} so far.` : "The fix isn't in a published release yet.";
  if (r.state === "regressed") return r.note ?? (rel ? `Came back after ${rel}.` : "Came back after the fix.");
  const rate = r.drop == null && r.before && r.after ? ` No rate yet: ${r.before.n} ${r.before.n === 1 ? "check" : "checks"} before, ${r.after.n} on it; Insights needs 10 a side.` : "";
  return `No finding in ${r.sessions} sessions on ${rel ?? "the fix's release"} or later.${rate}`;
}

/** The fixes across the full width: mistake, before → after, sessions saved, release, status. Phones get FixLine. */
function FixTable({ rows }: { rows: FixRow[] }) {
  const th = "text-muted-foreground border-b px-4 py-2 text-left whitespace-nowrap";
  const td = "border-b px-4 py-2 align-top";
  return (
    <table className="w-full border-collapse max-md:hidden">
      <thead>
        <tr>
          <th className={th}>Mistake</th>
          <th className={cn(th, "text-right")}>Missed before → after</th>
          <th className={cn(th, "text-right")}>Sessions saved</th>
          <th className={cn(th, "text-right")}>Release</th>
          <th className={th}>Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const worse = r.state === "regressed" || (r.drop != null && r.drop < 0);
          const saved = r.drop != null && r.after ? Math.round((r.drop / 100) * r.after.n) : null;
          return (
            <tr key={r.key} className="[&:last-child>td]:border-b-0">
              <td className={cn(td, "w-1/2")}>
                <HrefLink href={r.href} className="text-foreground font-semibold">
                  {r.name}
                </HrefLink>
                <div className="text-muted-foreground mt-1">{fixText(r)}</div>
              </td>
              <td className={cn(td, "text-right whitespace-nowrap tabular-nums")}>
                {r.drop != null ? (
                  <>
                    <span className="text-muted-foreground">{miss(r.before!)}%</span> → <span className={worse ? "text-[var(--miss)]" : "text-primary"}>{miss(r.after!)}%</span>
                  </>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
              <td className={cn(td, "text-right tabular-nums")}>
                {saved != null ? <span className={saved < 0 ? "text-[var(--miss)]" : ""}>{saved > 0 ? `${saved} fewer` : saved < 0 ? `${-saved} more` : "0"}</span> : <span className="text-muted-foreground">—</span>}
              </td>
              <td className={cn(td, "text-right tabular-nums")}>{r.release != null ? `#${r.release}` : <span className="text-muted-foreground">—</span>}</td>
              <td className={td}>
                <Tag tone={STATUS[r.state].tone || undefined}>{STATUS[r.state].label}</Tag>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function FixLine({ r }: { r: FixRow }) {
  const rel = r.release != null ? `release #${r.release}` : null;
  const back = r.state === "regressed";
  const worse = back || (r.drop != null && r.drop < 0);
  let line: ReactNode;
  if (r.state === "fixing")
    line = rel ? `Too early to tell: ${r.sessions} of ${r.needed} sessions on ${rel} so far.` : "Too early to tell: the fix isn't in a published release yet.";
  else if (back) line = r.note ?? (rel ? `Came back after ${rel}.` : "Came back after the fix.");
  else line = `Held: no finding in ${r.sessions} sessions on ${rel ?? "the fix's release"} or later.`;
  return (
    <li className="grid gap-2 border-b px-4 py-4 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_auto] sm:gap-x-6">
      <div className="min-w-0">
        <HrefLink href={r.href} className="text-foreground font-semibold">
          {r.name}
        </HrefLink>
        <div className="text-muted-foreground mt-1">
          {back && <span className="t-label mr-2 text-[var(--miss)]">Came back</span>}
          {line}
          {r.drop != null && r.after && (
            <>
              {" "}
              About {Math.abs(Math.round((r.drop / 100) * r.after.n))} {r.drop < 0 ? "more" : "fewer"} missed sessions on {rel}, over {r.after.n} checked.
            </>
          )}
          {r.drop == null && r.state !== "fixing" && r.before && r.after && (
            <> No before and after rate yet: {r.before.n} {r.before.n === 1 ? "check" : "checks"} before {rel}, {r.after.n} on it, and Insights needs 10 a side.</>
          )}
        </div>
      </div>
      {r.drop != null && (
        <div className="t-lead tabular-nums whitespace-nowrap sm:text-right">
          <span className="text-muted-foreground">{miss(r.before!)}%</span> → <span className={worse ? "text-[var(--miss)]" : "text-primary"}>{miss(r.after!)}%</span>
        </div>
      )}
    </li>
  );
}

/** This week's sessions that missed (deviated from a procedure), against the week before. */
function MissRate() {
  const { ws } = useWs();
  const res = useQuery(q.insightsOverview(ws, { window: "7d" }));
  const t = res.data?.tiles;
  const now = t?.current;
  const before = t?.prior;
  const delta = t?.delta_points.need_attention_pct;
  return (
    <Block title="Missed this week" aside={<HrefLink href={wsHref(ws, "results")} className="t-label text-foreground">Results →</HrefLink>} className="lg:col-span-5">
      <div className="px-4 py-4">
        {!now ? (
          <Note>{res.error ? "Insights didn't load." : "Loading…"}</Note>
        ) : !now.sessions_checked ? (
          <Note>No sessions were checked in the last 7 days.</Note>
        ) : (
          <>
            <div className="flex flex-wrap items-baseline gap-x-4">
              <span className="t-head text-[var(--miss)]">{fmtPct(now.need_attention_pct)}</span>
              <span className="text-muted-foreground">
                {now.need_attention} of {now.sessions_checked} checked sessions deviated
              </span>
            </div>
            <OutcomeBar followed={now.followed} partial={now.partial} deviated={now.need_attention} className="mt-4 h-2 w-full" />
            <dl className="mt-4 grid grid-cols-3 gap-4 tabular-nums">
              {(
                [
                  ["Followed", now.followed],
                  ["Partial", now.partial],
                  ["Deviated", now.need_attention],
                ] as const
              ).map(([k, v]) => (
                <div key={k}>
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
            <p className="text-muted-foreground mt-4 border-t pt-4">
              {before?.need_attention_pct == null || delta == null ? (
                "No checked sessions the week before, so nothing to compare with yet."
              ) : (
                <>
                  Week before: {fmtPct(before.need_attention_pct)} of {before.sessions_checked}.{" "}
                  <span className={cn("font-semibold", delta > 0 ? "text-[var(--miss)]" : delta < 0 ? "text-primary" : "")}>
                    {delta > 0 ? "+" : delta < 0 ? "−" : "±"}
                    {Math.abs(delta)} pts
                  </span>
                </>
              )}
            </p>
          </>
        )}
      </div>
    </Block>
  );
}

function LatestSessions() {
  const { ws } = useWs();
  const list = useInfiniteQuery(q.sessions(ws, {}));
  const rows = (list.data?.pages[0]?.items ?? []).slice(0, 6);
  return (
    <Block title="Latest sessions" aside={<HrefLink href={wsHref(ws, "sessions")} className="t-label text-foreground">All sessions →</HrefLink>} className="lg:col-span-7">
      {list.isPending ? (
        <Note className="block px-4 py-4">Loading…</Note>
      ) : rows.length ? (
        <ul>
          {rows.map((s) => (
            <li key={s.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b px-4 py-2 last:border-b-0 sm:grid-cols-[176px_minmax(0,1fr)_auto]">
              <HrefLink href={wsHref(ws, "sessions", s.id)} className="text-foreground whitespace-nowrap tabular-nums max-sm:col-span-2">
                {when(s.started_at)}
              </HrefLink>
              <span className="truncate">{s.agent_id ?? s.agent_ref ?? "Unmatched agent"}</span>
              <ResultTag result={s.result} />
            </li>
          ))}
        </ul>
      ) : (
        <Note className="block px-4 py-4">No sessions yet.</Note>
      )}
    </Block>
  );
}

export function HomeView() {
  const { ws, legacy, draft } = useWs();
  const mistakes = useQuery({ ...rq.review(ws), retry: false, enabled: !legacy });
  const needsReview = useNeedsReview(ws);
  const fixes = useQuery({ ...rq.suggestions(ws, "proposed"), retry: false, enabled: !legacy }).data?.items;
  return (
    <Page wide>
      <PageTitle>Home</PageTitle>
      <Sub>What needs you in {ws}, and how the last 7 days went.</Sub>
      <Block title="What needs you" className="mb-6 grid md:grid-cols-3 md:[&>header]:col-span-3">
        <Queue
          label="Mistakes to review"
          n={needsReview}
          items={(mistakes.data?.items ?? []).map((p) => ({ key: p.pattern_id, href: wsHref(ws, "patterns", p.pattern_id), text: p.name ?? "Unnamed mistake" }))}
          href={wsHref(ws, "patterns")}
          action="Review mistakes"
          empty="Nothing waiting for a verdict."
        />
        <Queue
          label="Fixes waiting"
          n={fixes ? fixes.length : null}
          items={(fixes ?? []).map((f) => ({ key: f.suggestion_id, href: wsHref(ws, "suggestions", f.suggestion_id), text: f.pattern_name ?? "Suggested change" }))}
          href={wsHref(ws, "suggestions")}
          action="Decide on fixes"
          empty="No drafted fixes waiting."
        />
        <Queue
          label="Unpublished changes"
          n={draft.items.length}
          items={draft.items.map((i) => ({ key: `${i.kind}/${i.id}`, href: wsHref(ws, "changes"), text: `${i.kind === "settings" ? "Settings" : `${KIND[i.kind].one} ${i.name || i.id}`}, ${i.change}` }))}
          href={wsHref(ws, "changes")}
          action="Review and publish"
          empty="Everything is published."
        />
      </Block>
      <div className="mb-6 grid items-start gap-6 lg:grid-cols-12">
        <MissRate />
        <LatestSessions />
      </div>
      <Fixed />
    </Page>
  );
}
