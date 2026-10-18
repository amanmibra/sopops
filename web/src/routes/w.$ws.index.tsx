import { Navigate, createFileRoute } from "@tanstack/react-router";
import { EmptyWorkspace } from "@/components/views";
import { useWs } from "@/lib/workspace";

export const Route = createFileRoute("/w/$ws/")({ component: WorkspaceHome });

/** A workspace opens in QA, on Insights; a new workspace says how to start. */
function WorkspaceHome() {
  const { ws, cfg } = useWs();
  if (!cfg.agents.length && !cfg.instructions.length && !cfg.procedures.length) return <EmptyWorkspace />;
  return <Navigate to="/w/$ws/insights" params={{ ws }} replace />;
}
