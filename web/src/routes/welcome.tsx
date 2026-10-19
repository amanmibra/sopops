import { useQueryClient } from "@tanstack/react-query";
import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { RedirectButtons } from "@/components/account";
import { StepShell } from "@/components/setup";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { type AuthState, call, sdk } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { setupHref, setupProgress } from "@/lib/setup";

type Step = "name" | "account" | "team";
type Search = { step?: Step };

export const Route = createFileRoute("/welcome")({
  validateSearch: (s: Record<string, unknown>): Search => ({ step: s.step === "account" || s.step === "team" ? s.step : undefined }),
  component: Welcome,
});

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const MIN_PASSWORD = 10;

/**
 * The first run, steps 1 to 3: your name, your email and password (the email is what you sign in with), and what the
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
  return <FirstRun auth={auth} start={step ?? "name"} />;
}

function FirstRun({ auth, start }: { auth: AuthState; start: Step }) {
  const [step, setStep] = useState<Step>(start === "team" ? "name" : start);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [team, setTeam] = useState("");
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const to = (s: Step) => {
    setError(null);
    setStep(s);
  };
  const accountProblem = () => {
    const e = email.trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e)) return "That doesn't look like an email address.";
    if (password.length < MIN_PASSWORD) return `Your password needs at least ${MIN_PASSWORD} characters.`;
    if (password.trim().toLowerCase() === e) return "Your password can't be your email address.";
    return null;
  };

  if (step === "name")
    return (
      <StepShell
        flow="first"
        step="name"
        sub="It's how your team sees you in sopops."
        primary={{ label: "Continue", disabled: !name.trim(), run: () => to("account") }}
      >
        <Label htmlFor="setup-name" className="sr-only">
          Your name
        </Label>
        <Input id="setup-name" autoFocus autoComplete="name" className="h-11 text-[15px]" placeholder="Ana Ruiz" value={name} onChange={(e) => setName(e.target.value)} />
      </StepShell>
    );

  if (step === "account")
    return (
      <StepShell
        flow="first"
        step="account"
        sub="You sign in with your email."
        back={() => to("name")}
        error={error}
        primary={{
          label: "Continue",
          disabled: !email.trim() || !password,
          run: () => {
            const problem = accountProblem();
            if (problem) setError(problem);
            else to("team");
          },
        }}
      >
        <div className="grid gap-3">
          <div className="grid gap-1">
            <Label htmlFor="setup-email" className="text-[13px]">
              Email
            </Label>
            <Input
              id="setup-email"
              type="email"
              autoFocus
              autoComplete="username"
              className="h-10"
              placeholder="name@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="setup-password" className="text-[13px]">
              Password
            </Label>
            <Input
              id="setup-password"
              type="password"
              autoComplete="new-password"
              className="h-10"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <span className="text-muted-foreground text-[12px]">At least {MIN_PASSWORD} characters.</span>
          </div>
        </div>
        {auth.providers.some((p) => p.redirects) && (
          <div className="mt-5 grid gap-2">
            <p className="text-muted-foreground text-[13px]">Or skip the password:</p>
            <RedirectButtons auth={auth} next="/welcome?step=team" />
          </div>
        )}
      </StepShell>
    );

  const create = async () => {
    setError(null);
    try {
      await call(sdk.firstAccount({ body: { name: name.trim(), email: email.trim(), password } }));
    } catch (e) {
      // the server's rules for the email and password are on step 2
      setStep("account");
      setError(message(e));
      return;
    }
    try {
      const made = await call(sdk.setUpOrg({ body: { team: team.trim() } }));
      setupProgress.set(made.workspace, "first", "people");
      qc.clear();
      window.location.assign(setupHref(made.workspace, "people", "first"));
    } catch (e) {
      setError(message(e));
      qc.clear();
      window.location.assign("/welcome?step=team"); // signed in now: the team step tries again
    }
  };
  return (
    <TeamForm team={team} setTeam={setTeam} back={() => to("account")} error={error} run={create} label="Create team" />
  );
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

function TeamForm({ team, setTeam, back, error, run, label }: { team: string; setTeam: (t: string) => void; back?: () => void; error: string | null; run: () => Promise<void>; label: string }) {
  return (
    <StepShell flow="first" step="team" sub="Use your company's name, or your team's." back={back} error={error} primary={{ label, disabled: !team.trim(), run }}>
      <Label htmlFor="setup-team" className="sr-only">
        Team name
      </Label>
      <Input id="setup-team" autoFocus autoComplete="organization" className="h-11 text-[15px]" placeholder="Acme Support" value={team} onChange={(e) => setTeam(e.target.value)} />
    </StepShell>
  );
}
