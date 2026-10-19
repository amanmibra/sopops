import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { GetSetUp, useChecklist } from "@/components/checklist";
import { EmptyWorkspace } from "@/components/views";
import { useAuth } from "@/lib/auth";
import { useWs } from "@/lib/workspace";

export const Route = createFileRoute("/w/$ws/")({ component: WorkspaceHome });

/**
 * A team's home. A new one first asks which platforms the team uses (if nobody has said), then shows "Get set up"
 * until that's done or hidden. After that it opens in QA, on Insights, or says how to start when there's nothing yet.
 */
function WorkspaceHome() {
  const { ws, cfg } = useWs();
  const auth = useAuth().data;
  const { show, items } = useChecklist();
  const [, hidden] = useState(0);
  const empty = !cfg.agents.length && !cfg.instructions.length && !cfg.procedures.length;
  if (empty && auth?.platforms && auth.platforms.selected === null) return <Navigate to="/w/$ws/setup" params={{ ws }} search={{ step: "platforms", flow: "solo" }} replace />;
  if (!items) return <div className="text-muted-foreground py-6">Loading…</div>;
  if (show) return <GetSetUp items={items} empty={empty} onHide={() => hidden((n) => n + 1)} />;
  if (empty) return <EmptyWorkspace />;
  return <Navigate to="/w/$ws/insights" params={{ ws }} replace />;
}
