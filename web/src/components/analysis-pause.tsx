// Settings → Checks: pause the workspace's analysis. Calls keep coming in and their transcripts show, but nothing is
// routed, checked, clustered or suggested until it's resumed, which checks what came in meanwhile. Owners and admins
// only: the panel isn't there for anyone else (the server refuses them too).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { call, q, sdk } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { plural, when } from "@/lib/kinds";
import { useWs } from "@/lib/workspace";
import { Banner, ConfirmButton, ErrorBanner, Panel } from "./bits";
import { Button } from "./ui/button";

export function AnalysisPausePanel() {
  const auth = useAuth().data;
  const role = auth?.org?.role;
  // with sign-in off there are no roles, and the server lets anyone change it
  const manager = Boolean(auth) && (!auth?.required || role === "owner" || role === "admin");
  if (!manager) return null;
  return <PauseSwitch />;
}

function PauseSwitch() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const state = useQuery(q.analysis(ws));
  const [busy, setBusy] = useState(false);

  const set = async (paused: boolean) => {
    setBusy(true);
    try {
      const next = await call(sdk.setAnalysis({ path: { workspace: ws }, body: { paused } }));
      qc.setQueryData(q.analysis(ws).queryKey, next);
      if (paused) toast.success("Analysis paused. Calls still come in.");
      else toast.success(next.queued ? `Analysis resumed. Checking ${plural(next.queued, "call", "calls")} that came in while it was paused.` : "Analysis resumed.");
      void qc.invalidateQueries({ queryKey: ["ws", ws, "sessions"] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const data = state.data;
  return (
    <Panel title="Pause analysis">
      {state.isError && <ErrorBanner error={state.error} lead="Whether analysis is paused didn't load:" />}
      {data?.paused && (
        <Banner tone="warn">
          Paused {when(data.paused_at)}
          {data.paused_by ? ` by ${data.paused_by}` : ""}. Calls are coming in, and none are being checked.
        </Banner>
      )}
      <p className="text-muted-foreground mb-3 text-sm">
        Calls keep coming in and their transcripts show in Sessions, but no AI runs on them: no checks, patterns, suggestions or calibration. Resuming checks
        the calls that came in while it was paused.
      </p>
      {data?.paused ? (
        <Button disabled={busy} onClick={() => void set(false)}>
          Resume analysis
        </Button>
      ) : (
        <ConfirmButton variant="outline" disabled={busy || !data} confirm="Click again to pause" onConfirm={() => void set(true)}>
          Pause analysis
        </ConfirmButton>
      )}
    </Panel>
  );
}
