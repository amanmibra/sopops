// Backfill, inside a platform's row in Settings → Platforms: once its API key is saved, bring the last 7 days of
// ElevenLabs or Vapi calls in as sessions. What it would bring in first, then the run's progress, then where they are.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { History } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError, type BackfillOut, type BackfillPreview, call, sdk } from "@/lib/api";
import { platformName, plural, when } from "@/lib/kinds";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ErrorBanner, Note } from "./bits";
import { HrefLink } from "./href-link";
import { Button } from "./ui/button";

type Platform = BackfillOut["platform"];

const DAYS = 7;
const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const keyOf = (ws: string, platform: Platform, what: string) => ["ws", ws, "backfill", platform, what];
const usd = (n: number) => (n < 0.01 ? "under $0.01" : `about $${n.toFixed(2)}`);

export function Backfill({ platform }: { platform: Platform }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const name = platformName(platform);
  const latest = useQuery({
    queryKey: keyOf(ws, platform, "latest"),
    queryFn: () => call(sdk.getBackfill({ path: { workspace: ws, platform } })),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 2000 : false),
  });
  const running = latest.data?.status === "running";
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) qc.invalidateQueries({ queryKey: keyOf(ws, platform, "preview") }); // finished: count again
    wasRunning.current = running;
  }, [running, qc, ws, platform]);
  const preview = useQuery({
    queryKey: keyOf(ws, platform, "preview"),
    queryFn: () => call(sdk.backfillPreview({ path: { workspace: ws, platform }, query: { days: DAYS } })),
    enabled: latest.isSuccess && !running,
    staleTime: 60_000,
    retry: false,
  });

  const start = async () => {
    setBusy(true);
    try {
      const run = await call(sdk.startBackfill({ path: { workspace: ws, platform }, body: { days: DAYS } }));
      qc.setQueryData(keyOf(ws, platform, "latest"), run);
    } catch (e) {
      toast.error(msg(e));
    } finally {
      setBusy(false);
    }
  };
  const recount = () => qc.invalidateQueries({ queryKey: keyOf(ws, platform, "preview") });
  const denied = [latest.error, preview.error].some((e) => e instanceof ApiError && e.status === 403);

  return (
    <div className="grid gap-2 rounded-md border px-3 py-2.5 text-[13px]">
      <div className="flex items-center gap-2 font-medium">
        <History className="size-4" /> Backfill the last {DAYS} days
      </div>
      <p className="text-muted-foreground max-w-prose leading-relaxed">
        Bring in the calls your agents had on {name} in the last {DAYS} days, so there are sessions to check right away. sopops reads them with the API key
        above. Calls already in sopops are skipped, so you can run it again.
      </p>
      {denied ? (
        <Note>Only the org's owners and admins can backfill calls.</Note>
      ) : latest.isError ? (
        <ErrorBanner error={latest.error} />
      ) : running && latest.data ? (
        <Progress run={latest.data} />
      ) : (
        <>
          {latest.data && <Finished run={latest.data} ws={ws} />}
          {preview.isPending && latest.isSuccess && <Note>Counting the calls on {name}…</Note>}
          {preview.isError && <ErrorBanner error={preview.error} />}
          {preview.data && <Preview p={preview.data} busy={busy} onStart={start} onRecount={recount} counting={preview.isFetching} />}
        </>
      )}
    </div>
  );
}

function Preview({ p, busy, counting, onStart, onRecount }: { p: BackfillPreview; busy: boolean; counting: boolean; onStart: () => void; onRecount: () => void }) {
  if (!p.key_set) return <Note>{p.error}</Note>;
  if (!p.agents.length) return <Note>No agents in this team run on {platformName(p.platform)} yet. Add one, then come back.</Note>;
  const failed = p.agents.filter((a) => a.error);
  return (
    <div className="grid gap-2">
      <p>
        Found <b>{plural(p.found, "call", "calls")}</b> from {plural(p.agents.length, "agent", "agents")}: <b>{p.to_import} new</b>
        {p.already_imported > 0 && `, ${p.already_imported} already in sopops`}.
        {p.agents.some((a) => a.capped) && " There may be more: sopops reads up to 2,000 calls per agent."}
      </p>
      {p.to_import > 0 && p.estimated_cost_usd != null && (
        <p className="text-muted-foreground">
          Checking them costs {usd(p.estimated_cost_usd)}, from what this team spent per session in the last 30 days.
        </p>
      )}
      {failed.length > 0 && (
        <Banner tone="warn" className="mb-0">
          {failed.map((a) => (
            <div key={a.agent}>
              <b>{a.agent}</b>: {a.error}
            </div>
          ))}
        </Banner>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy || p.to_import === 0} onClick={onStart}>
          {p.to_import ? `Backfill ${plural(p.to_import, "call", "calls")}` : "Nothing new to backfill"}
        </Button>
        <Button size="sm" variant="ghost" disabled={counting} onClick={onRecount}>
          Count again
        </Button>
      </div>
    </div>
  );
}

function Progress({ run }: { run: BackfillOut }) {
  const done = run.imported + run.skipped + run.failed;
  const pct = run.found ? Math.round((100 * done) / run.found) : 0;
  return (
    <div className="grid gap-2" aria-live="polite">
      <p>
        {run.found ? (
          <>
            Bringing in calls: <b>{done}</b> of {run.found}. {run.imported} new, {run.skipped} already in sopops
            {run.failed > 0 && `, ${run.failed} failed`}.
          </>
        ) : (
          "Listing the calls…"
        )}
      </p>
      <div className="bg-muted h-1.5 overflow-hidden rounded-full" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="bg-primary h-full transition-[width]" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-muted-foreground text-xs">You can leave this page. It keeps going.</p>
    </div>
  );
}

function Finished({ run, ws }: { run: BackfillOut; ws: string }) {
  const errors = run.agents.filter((a) => a.error);
  return (
    <Banner tone={run.status === "failed" ? "err" : run.failed ? "warn" : "ok"} className="mb-0">
      {run.status === "failed" ? (
        <b>The last backfill didn't finish. </b>
      ) : (
        <>
          <b>Backfill done.</b> {plural(run.imported, "new session", "new sessions")}
          {run.skipped > 0 && `, ${run.skipped} already in sopops`}
          {run.failed > 0 && `, ${run.failed} couldn't be read`}.{" "}
        </>
      )}
      {run.error}
      {run.imported > 0 && (
        <>
          {" "}
          <HrefLink href={wsHref(ws, "sessions")}>See them in Sessions</HrefLink>
        </>
      )}
      {errors.length > 0 && (
        <ul>
          {errors.map((a) => (
            <li key={a.agent}>
              {a.agent}: {a.error}
            </li>
          ))}
        </ul>
      )}
      <span className="text-muted-foreground block text-xs">
        {run.finished_at ? `Finished ${when(run.finished_at)}` : `Started ${when(run.created_at)}`}
        {run.created_by && ` by ${run.created_by}`}.
      </span>
    </Banner>
  );
}
