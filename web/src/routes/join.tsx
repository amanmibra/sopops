import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Card } from "@/components/account";
import { Banner } from "@/components/bits";
import { StepShell } from "@/components/setup";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { type AuthState, type JoinInfo, call, sdk } from "@/lib/api";
import { pendingJoin, useAuth } from "@/lib/auth";
import { FLOWS, setupHref } from "@/lib/setup";
import { appHref, assignApp } from "@/lib/paths";

export const Route = createFileRoute("/join")({ component: Join });

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const MIN_PASSWORD = 10;
const asRole = (r: string) => (r === "member" ? "a member" : `an ${r}`);

/** The join link's token: after # in the link (so it never reaches the server's logs), or kept while signing in. */
function useJoinToken(): string | null {
  const [token] = useState(() => {
    const fromLink = decodeURIComponent(window.location.hash.slice(1));
    if (fromLink) {
      pendingJoin.set(fromLink);
      window.history.replaceState(null, "", "/join");
    }
    return fromLink || pendingJoin.get();
  });
  return token;
}

/** Where to go once you're in the team: its platforms first if nobody has picked them. */
function into(state: AuthState, needsPlatforms: boolean): string {
  const ws = state.org?.workspace;
  if (!ws) return "/";
  return needsPlatforms ? setupHref(ws, "platforms", "join") : `/w/${encodeURIComponent(ws)}`;
}

function Join() {
  const token = useJoinToken();
  const auth = useAuth().data;
  const info = useQuery({
    queryKey: ["join", token],
    queryFn: () => call(sdk.lookupJoinLink({ body: { token: token ?? "" } })),
    enabled: Boolean(token),
    retry: false,
  });
  if (!auth) return null;
  if (!token || info.isError)
    return (
      <Card title="This join link doesn't work">
        <p className="text-muted-foreground text-sm leading-relaxed">It may have expired or been turned off. Ask whoever sent it for a new one.</p>
      </Card>
    );
  if (!info.data) return <Card title="Opening your link…">{null}</Card>;
  if (auth.user) return <JoinSignedIn auth={auth} info={info.data} token={token} />;
  return <JoinNew auth={auth} info={info.data} token={token} />;
}

/** Signed in already: join the team too, or not. Nothing about your other teams changes. */
function JoinSignedIn({ auth, info, token }: { auth: AuthState; info: JoinInfo; token: string }) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const join = async () => {
    setBusy(true);
    setError(null);
    try {
      const state = await call(sdk.acceptJoinLink({ body: { token } }));
      pendingJoin.set(null);
      qc.clear();
      assignApp(into(state, false)); // the team's home asks for platforms if nobody has picked
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  };
  const current = auth.org?.name;
  if (info.member)
    return (
      <Card title={`You're in ${info.org_name}`}>
        <p className="text-muted-foreground mb-5 text-sm leading-relaxed">You joined this team already.</p>
        {error && <Banner tone="err">{error}</Banner>}
        <Button disabled={busy} onClick={() => void join()}>
          Open {info.org_name}
        </Button>
      </Card>
    );
  return (
    <Card title={`Join ${info.org_name}?`}>
      <p className="mb-3 text-sm leading-relaxed">
        You're signed in as <b>{auth.user?.email}</b>. You'll join as {asRole(info.role)}.
      </p>
      {current && (
        <p className="text-muted-foreground mb-5 text-sm leading-relaxed">
          You stay in {current}, with everything in it. Switch between your teams from your account menu, top right.
        </p>
      )}
      {error && <Banner tone="err">{error}</Banner>}
      <div className="flex flex-wrap gap-2">
        <Button disabled={busy} onClick={() => void join()}>
          Join {info.org_name}
        </Button>
        <Button
          variant="outline"
          onClick={() => {
            pendingJoin.set(null);
            assignApp("/");
          }}
        >
          Not now
        </Button>
      </div>
    </Card>
  );
}

/** No account yet: your name, then an email and password, and you're in. Already have one? Sign in, then join. */
function JoinNew({ auth, info, token }: { auth: AuthState; info: JoinInfo; token: string }) {
  const [step, setStep] = useState<"name" | "account">("name");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  useEffect(() => setError(null), [step]);
  const passwords = auth.providers.some((p) => p.id === "password");
  // A team whose platforms are picked already has two steps for you; otherwise a third asks for them.
  const total = info.needs_platforms ? FLOWS.join.length : FLOWS.join.length - 1;
  const signIn = (
    <p className="text-muted-foreground mt-4 text-sm">
      Have an account here?{" "}
      <a className="text-primary underline-offset-2 hover:underline" href={appHref(`/sign-in?next=${encodeURIComponent("/join")}`)}>
        Sign in to join
      </a>
      .
    </p>
  );
  if (!passwords)
    return (
      <Card title={`Join ${info.org_name} on sopops`}>
        <p className="text-muted-foreground mb-4 text-sm">Sign in, and you'll join as {asRole(info.role)}.</p>
        <Button asChild>
          <a href={appHref(`/sign-in?next=${encodeURIComponent("/join")}`)}>Sign in to join</a>
        </Button>
      </Card>
    );
  const sub = (text: string) => (
    <>
      You're joining <b className="text-foreground">{info.org_name}</b> as {asRole(info.role)}. {text}
    </>
  );
  if (step === "name")
    return (
      <StepShell flow="join" step="name" total={total} sub={sub("It's how your team sees you.")} primary={{ label: "Continue", disabled: !name.trim(), run: () => setStep("account") }}>
        <Label htmlFor="join-name" className="sr-only">
          Your name
        </Label>
        <Input id="join-name" autoFocus autoComplete="name" className="h-12 text-base" value={name} onChange={(e) => setName(e.target.value)} />
        {signIn}
      </StepShell>
    );
  const join = async () => {
    setError(null);
    const e = email.trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e)) return setError("That doesn't look like an email address.");
    if (password.length < MIN_PASSWORD) return setError(`Your password needs at least ${MIN_PASSWORD} characters.`);
    try {
      const state = await call(sdk.joinWithLink({ body: { token, name: name.trim(), email: email.trim(), password } }));
      pendingJoin.set(null);
      qc.clear();
      assignApp(into(state, info.needs_platforms));
    } catch (err) {
      setError(message(err));
    }
  };
  return (
    <StepShell
      flow="join"
      step="account"
      total={total}
      sub="You sign in with your email."
      back={() => setStep("name")}
      error={error}
      primary={{ label: `Join ${info.org_name}`, disabled: !email.trim() || !password, run: join }}
    >
      <div className="grid gap-3">
        <div className="grid gap-1">
          <Label htmlFor="join-email" className="text-sm">
            Email
          </Label>
          <Input id="join-email" type="email" autoFocus autoComplete="username" className="h-10" placeholder="name@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="join-password" className="text-sm">
            Password
          </Label>
          <Input id="join-password" type="password" autoComplete="new-password" className="h-10" value={password} onChange={(e) => setPassword(e.target.value)} />
          <span className="text-muted-foreground text-sm">At least {MIN_PASSWORD} characters.</span>
        </div>
      </div>
      {signIn}
    </StepShell>
  );
}
