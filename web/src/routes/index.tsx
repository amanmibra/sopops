import { useQuery } from "@tanstack/react-query";
import { Navigate, createFileRoute } from "@tanstack/react-router";
import { ErrorBanner } from "@/components/bits";
import { q } from "@/lib/api";
import { prefs } from "@/lib/prefs";

export const Route = createFileRoute("/")({ component: Home });

/** Open the last workspace (or the first); with none, say how to start. */
function Home() {
  const workspaces = useQuery(q.workspaces());
  if (workspaces.isPending) return <main className="text-muted-foreground px-8 py-6">Loading…</main>;
  if (workspaces.isError)
    return (
      <main className="px-8 py-6">
        <ErrorBanner error={workspaces.error} />
      </main>
    );
  const names = workspaces.data.map((w) => w.workspace);
  const last = prefs.get("ws");
  const ws = last && names.includes(last) ? last : names[0];
  if (ws) return <Navigate to="/w/$ws" params={{ ws }} replace />;
  return (
    <main className="max-w-[60ch] px-8 py-10">
      <h1 className="mb-2 text-[22px] font-semibold tracking-tight">Create a workspace</h1>
      <p>A workspace holds your agents and the shared instructions, procedures and groups they're built from. Use New workspace at the top to start one.</p>
    </main>
  );
}
