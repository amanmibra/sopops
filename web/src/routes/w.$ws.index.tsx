import { Navigate, createFileRoute } from "@tanstack/react-router";
import { EmptyWorkspace } from "@/components/views";
import { useWs } from "@/lib/workspace";

export const Route = createFileRoute("/w/$ws/")({ component: WorkspaceHome });

/** Unpublished changes if there are any, else the overview; a new workspace says how to start. */
function WorkspaceHome() {
  const { ws, cfg, draft } = useWs();
  if (!cfg.agents.length && !cfg.instructions.length && !cfg.procedures.length) return <EmptyWorkspace />;
  return draft.items.length ? <Navigate to="/w/$ws/changes" params={{ ws }} replace /> : <Navigate to="/w/$ws/overview" params={{ ws }} replace />;
}
