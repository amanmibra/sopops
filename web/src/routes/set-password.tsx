import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Card, NewPassword, useSignedIn } from "@/components/account";
import { Banner } from "@/components/bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { call, sdk } from "@/lib/api";
import { appHref } from "@/lib/paths";

export const Route = createFileRoute("/set-password")({ component: SetPassword });

/**
 * A link that sets a password lands here: one an owner or admin made, one from `sopops reset-password`, or one emailed
 * from "Forgot password?" (or to make an account). The token is after #, so it never reaches the server's logs.
 */
function SetPassword() {
  const signedIn = useSignedIn();
  const [token] = useState(() => {
    const t = decodeURIComponent(window.location.hash.slice(1));
    if (t) window.history.replaceState(null, "", "/set-password"); // out of the address bar and history
    return t;
  });
  const info = useQuery({
    queryKey: ["password-link", token],
    queryFn: () => call(sdk.lookupPasswordLink({ body: { token } })),
    enabled: Boolean(token),
    retry: false,
  });
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!token || info.isError)
    return (
      <Card title="This link doesn't work">
        <p className="text-muted-foreground mb-5 text-sm leading-relaxed">
          It may have expired (links work for an hour) or been used already. Ask an owner or admin for a new one, or use "Forgot password?" on the sign-in page
          if it's there.
        </p>
        <Button asChild variant="outline">
          <a href={appHref("/sign-in")}>Go to sign in</a>
        </Button>
      </Card>
    );
  if (!info.data) return <Card title="Opening your link…">{null}</Card>;
  const { email, has_account } = info.data;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const got = await call(sdk.setPassword({ body: { token, password, name: name.trim() } }));
      signedIn(got.next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };
  return (
    <Card title={has_account ? "Set a new password" : "Make your account"}>
      <p className="text-muted-foreground mb-5 text-sm leading-relaxed">
        For <b className="text-foreground">{email}</b>. {has_account ? "Setting it signs you out everywhere else." : "Then you're signed in."} This link works
        once.
      </p>
      <form
        className="grid gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <input type="email" hidden readOnly autoComplete="username" value={email} />
        {!has_account && (
          <div className="grid gap-1">
            <Label htmlFor="set-name" className="text-sm">
              Name
            </Label>
            <Input id="set-name" autoComplete="name" className="h-10" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
        )}
        <NewPassword id="set-password" label={has_account ? "New password" : "Password"} value={password} onChange={setPassword} />
        {error && <Banner tone="err">{error}</Banner>}
        <Button type="submit" className="h-10 text-sm" disabled={busy || password.length < 10}>
          {has_account ? "Set password and sign in" : "Create account and sign in"}
        </Button>
      </form>
    </Card>
  );
}
