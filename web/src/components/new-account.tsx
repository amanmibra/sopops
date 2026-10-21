// Making an account and a team, steps 1 to 3 of the first run: your name, your email and password (the email is what
// you sign in with), and what the team is called. The first person on a fresh install does it on /welcome; with
// SOPOPS_OPEN_SIGNUP on, anyone does it on /sign-up. Either way, the team's setup goes on from step 4.
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { type AuthState, call, sdk } from "@/lib/api";
import { setupHref, setupProgress } from "@/lib/setup";
import { RedirectButtons } from "./account";
import { StepShell } from "./setup";
import { Input } from "./ui/input";
import { Label } from "./ui/label";

export type AccountStep = "name" | "account" | "team";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const MIN_PASSWORD = 10;

/** Into the team's setup, at step 4, with nothing cached from before. */
function intoSetup(ws: string) {
  setupProgress.set(ws, "first", "people");
  window.location.assign(setupHref(ws, "people", "first"));
}

/**
 * `signUp`: open sign-up, which makes the account and its team in one go. Otherwise the first account on a fresh
 * install, which then names the install's team; GitHub and Google can stand in for the password there.
 */
export function NewAccount({ auth, start, signUp = false }: { auth: AuthState; start: AccountStep; signUp?: boolean }) {
  const [step, setStep] = useState<AccountStep>(start === "team" ? "name" : start);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [team, setTeam] = useState("");
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const to = (s: AccountStep) => {
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
  const signIn = signUp && (
    <p className="text-muted-foreground mt-4 text-sm">
      Have an account?{" "}
      <a className="text-primary underline-offset-2 hover:underline" href="/sign-in">
        Sign in
      </a>
    </p>
  );

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
        <Input id="setup-name" autoFocus autoComplete="name" className="h-12 text-base" placeholder="Ana Ruiz" value={name} onChange={(e) => setName(e.target.value)} />
        {signIn}
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
            <Label htmlFor="setup-email" className="text-sm">
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
            <Label htmlFor="setup-password" className="text-sm">
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
            <span className="text-muted-foreground text-sm">At least {MIN_PASSWORD} characters.</span>
          </div>
        </div>
        {!signUp && auth.providers.some((p) => p.redirects) && (
          <div className="mt-5 grid gap-2">
            <p className="text-muted-foreground text-sm">Or skip the password:</p>
            <RedirectButtons auth={auth} next="/welcome?step=team" />
          </div>
        )}
        {signIn}
      </StepShell>
    );

  const body = { name: name.trim(), email: email.trim(), password };
  const create = async () => {
    setError(null);
    if (signUp) {
      try {
        const made = await call(sdk.signUp({ body: { ...body, team: team.trim() } }));
        qc.clear();
        if (made.org?.workspace) intoSetup(made.org.workspace);
        else window.location.assign("/");
      } catch (e) {
        // the email, the password, or too many tries: all on step 2
        setStep("account");
        setError(message(e));
      }
      return;
    }
    try {
      await call(sdk.firstAccount({ body }));
    } catch (e) {
      // the server's rules for the email and password are on step 2
      setStep("account");
      setError(message(e));
      return;
    }
    try {
      const made = await call(sdk.setUpOrg({ body: { team: team.trim() } }));
      qc.clear();
      intoSetup(made.workspace);
    } catch (e) {
      setError(message(e));
      qc.clear();
      window.location.assign("/welcome?step=team"); // signed in now: the team step tries again
    }
  };
  return <TeamForm team={team} setTeam={setTeam} back={() => to("account")} error={error} run={create} label="Create team" />;
}

/** Step 3: what the team is called. */
export function TeamForm({
  team,
  setTeam,
  back,
  error,
  run,
  label,
}: {
  team: string;
  setTeam: (t: string) => void;
  back?: () => void;
  error: string | null;
  run: () => Promise<void>;
  label: string;
}) {
  return (
    <StepShell flow="first" step="team" sub="Use your company's name, or your team's." back={back} error={error} primary={{ label, disabled: !team.trim(), run }}>
      <Label htmlFor="setup-team" className="sr-only">
        Team name
      </Label>
      <Input id="setup-team" autoFocus autoComplete="organization" className="h-12 text-base" placeholder="Acme Support" value={team} onChange={(e) => setTeam(e.target.value)} />
    </StepShell>
  );
}
