import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Card, SignInButtons } from "@/components/account";
import { Banner } from "@/components/bits";
import { Button } from "@/components/ui/button";
import { call, sdk } from "@/lib/api";
import { pendingInvite, useAuth, useSignOut } from "@/lib/auth";

export const Route = createFileRoute("/invite")({ component: Invite });

/** The invite link's token: after # in the link (so it never reaches the server's logs), or kept while signing in. */
function useInviteToken(): string | null {
  const [token] = useState(() => {
    const fromLink = decodeURIComponent(window.location.hash.slice(1));
    if (fromLink) {
      pendingInvite.set(fromLink);
      window.history.replaceState(null, "", "/invite"); // out of the address bar and history
    }
    return fromLink || pendingInvite.get();
  });
  return token;
}

function Invite() {
  const token = useInviteToken();
  const auth = useAuth().data;
  const qc = useQueryClient();
  const signOut = useSignOut("/invite");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const info = useQuery({
    queryKey: ["invite", token],
    queryFn: () => call(sdk.lookupInvite({ body: { token: token ?? "" } })),
    enabled: Boolean(token),
    retry: false,
  });
  // Used by you, or for you while you're already in the org.
  const joined = Boolean(
    auth?.user && info.data && (info.data.accepted || (auth.user.email === info.data.email && auth.orgs.some((o) => o.id === info.data.org_id))),
  );
  useEffect(() => {
    if (joined) pendingInvite.set(null);
  }, [joined]);
  if (!auth) return null;

  if (!token || info.isError)
    return (
      <Card title="This invite link doesn't work">
        <p className="text-muted-foreground text-[13px] leading-relaxed">
          It may have expired (links work for 7 days), been used already, or been withdrawn. Ask whoever sent it for a new one.
        </p>
      </Card>
    );
  if (!info.data) return <Card title="Opening your invite…">{null}</Card>;
  const inv = info.data;

  if (joined)
    return (
      <Card title={`You're in ${inv.org_name}`}>
        <p className="text-muted-foreground mb-5 text-[13px]">You joined as {inv.role === "member" ? "a member" : `an ${inv.role}`}.</p>
        <Button asChild>
          <a href="/">Open sopops</a>
        </Button>
      </Card>
    );

  const accept = async () => {
    setBusy(true);
    setError(null);
    try {
      await call(sdk.acceptInvite({ body: { token } }));
      pendingInvite.set(null);
      await qc.invalidateQueries();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={`Join ${inv.org_name} on sopops`}>
      <p className="text-muted-foreground mb-5 text-[13px] leading-relaxed">
        This invite is for <b className="text-foreground">{inv.email}</b>, as {inv.role === "member" ? "a member" : `an ${inv.role}`}.
      </p>
      {error && (
        <Banner tone="err" className="mb-4">
          {error}
        </Banner>
      )}
      {!auth.user ? (
        <>
          <p className="mb-3 text-[13px]">Sign in with {inv.email} to join.</p>
          <SignInButtons auth={auth} next="/invite" />
        </>
      ) : auth.user.email === inv.email ? (
        <Button disabled={busy} onClick={() => void accept()}>
          Join {inv.org_name}
        </Button>
      ) : (
        <>
          <p className="mb-4 text-[13px] leading-relaxed">
            You're signed in as <b>{auth.user.email}</b>. Sign out, then sign in with {inv.email} to join.
          </p>
          <Button variant="outline" onClick={() => void signOut()}>
            Sign out
          </Button>
        </>
      )}
    </Card>
  );
}
