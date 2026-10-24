import { useQueryClient } from "@tanstack/react-query";
import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { type AccountStep, NewAccount, TeamForm } from "@/components/new-account";
import { type AuthState, call, sdk } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { setupHref, setupProgress } from "@/lib/setup";

type Search = { step?: AccountStep };

export const Route = createFileRoute("/welcome")({
  validateSearch: (s: Record<string, unknown>): Search => ({ step: s.step === "account" || s.step === "team" ? s.step : undefined }),
  component: Welcome,
});

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/**
 * The first run, steps 1 to 3: your email and confirmed password (the email is what you sign in with), your name, and what the
 * team is called, which makes your account (the owner), names the team and makes its workspace. Signed in already
 * (GitHub or Google on a fresh install, or Back from step 4), only the team step is left.
 */
function Welcome() {
  const auth = useAuth().data;
  const { step } = Route.useSearch();
  if (!auth) return null;
  if (auth.user) {
    const manager = auth.org?.role === "owner" || auth.org?.role === "admin";
    if (!manager) return <Navigate to="/" replace />;
    return <TeamStep auth={auth} />;
  }
  if (!auth.first_account) return <Navigate to="/sign-in" replace />;
  return <NewAccount auth={auth} start={step ?? "account"} />;
}

/** Signed in: name the team (and make its workspace), then on to step 4. */
function TeamStep({ auth }: { auth: AuthState }) {
  const named = auth.org && auth.org.name !== "Default" ? auth.org.name : "";
  const [team, setTeam] = useState(named);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const run = async () => {
    setError(null);
    try {
      const made = await call(sdk.setUpOrg({ body: { team: team.trim() } }));
      setupProgress.set(made.workspace, "first", "people");
      await qc.invalidateQueries();
      window.location.assign(setupHref(made.workspace, "people", "first"));
    } catch (e) {
      setError(message(e));
    }
  };
  return <TeamForm team={team} setTeam={setTeam} error={error} run={run} label="Continue" />;
}
