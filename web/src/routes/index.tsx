import { useQuery } from "@tanstack/react-query";
import { Navigate, createFileRoute } from "@tanstack/react-router";
import { Card } from "@/components/account";
import { ErrorBanner } from "@/components/bits";
import { q } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { prefs } from "@/lib/prefs";

export const Route = createFileRoute("/")({ component: Home });

/** Straight into your team. With sign-in off, the last workspace opened (or the first, or a new one). */
function Home() {
  const auth = useAuth().data;
  if (!auth) return null;
  if (auth.required) return <TeamHome />;
  return <LocalHome />;
}

function TeamHome() {
  const auth = useAuth().data!;
  const ws = auth.org?.workspace;
  if (ws) return <Navigate to="/w/$ws" params={{ ws }} replace />;
  const manager = auth.org?.role === "owner" || auth.org?.role === "admin";
  if (manager) return <Navigate to="/welcome" search={{ step: "team" }} replace />;
  return (
    <Card title={`${auth.org?.name ?? "Your team"} isn't set up yet`}>
      <p className="text-muted-foreground text-sm leading-relaxed">Ask an owner or admin of your team to finish setting it up, then open sopops again.</p>
    </Card>
  );
}

function LocalHome() {
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
  const ws = last && names.includes(last) ? last : (names[0] ?? "default");
  return <Navigate to="/w/$ws" params={{ ws }} replace />;
}
