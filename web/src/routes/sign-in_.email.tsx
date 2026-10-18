import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Card } from "@/components/account";
import { Banner } from "@/components/bits";
import { Button } from "@/components/ui/button";
import { call, sdk } from "@/lib/api";

export const Route = createFileRoute("/sign-in_/email")({ component: EmailLink });

/**
 * An emailed sign-in link lands here with its token after #, which browsers never send to a server. Signing in takes a
 * click, so a mail scanner that opens the link (even one that runs this page) can't use it up.
 */
function EmailLink() {
  const qc = useQueryClient();
  const [token] = useState(() => {
    const t = decodeURIComponent(window.location.hash.slice(1));
    if (t) window.history.replaceState(null, "", "/sign-in/email"); // out of the address bar and history
    return t;
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!token || error)
    return (
      <Card title="This sign-in link doesn't work">
        <p className="text-muted-foreground mb-5 text-[13px] leading-relaxed">
          {error ? `${error.charAt(0).toUpperCase()}${error.slice(1)}.` : "It's missing its code."} Links work once, for 15 minutes.
        </p>
        <Button asChild variant="outline">
          <a href="/sign-in">Get a new link</a>
        </Button>
      </Card>
    );
  const signIn = async () => {
    setBusy(true);
    try {
      const got = await call(sdk.confirmEmailLink({ body: { token } }));
      qc.clear();
      window.location.assign(got.next.startsWith("/sign-in") ? "/" : got.next);
    } catch (e) {
      setError((e instanceof Error ? e.message : String(e)).replace(/\.$/, ""));
      setBusy(false);
    }
  };
  return (
    <Card title="Sign in to sopops">
      <p className="text-muted-foreground mb-5 text-[13px] leading-relaxed">You opened the link from your email. Click below to finish signing in.</p>
      {busy && <Banner className="mb-4">Signing you in…</Banner>}
      <Button className="h-10 w-full text-[14px]" disabled={busy} onClick={() => void signIn()}>
        Sign in
      </Button>
    </Card>
  );
}
