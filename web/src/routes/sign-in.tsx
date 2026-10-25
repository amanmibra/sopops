import { Link, Navigate, createFileRoute } from "@tanstack/react-router";
import { useEffect } from "react";
import { Card, SignInButtons } from "@/components/account";
import { Banner } from "@/components/bits";
import { useGo } from "@/components/href-link";
import { signInHref, useAuth } from "@/lib/auth";

type Search = { next?: string; error?: string };

export const Route = createFileRoute("/sign-in")({
  validateSearch: (s: Record<string, unknown>): Search => ({
    next: typeof s.next === "string" ? s.next : undefined,
    error: typeof s.error === "string" ? s.error : undefined,
  }),
  component: SignIn,
});

/** Only paths on this site: anything else goes home. */
const safeNext = (next?: string) => (next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/sign-in") ? next : "/");

function SignIn() {
  const { next, error } = Route.useSearch();
  const auth = useAuth().data;
  const go = useGo();
  const done = Boolean(auth && (!auth.required || auth.user)); // sign-in is off, or you're in already
  // One provider, and it redirects: there is nothing to choose, so don't show a page with a single button.
  // The provider's own pages are the sign-in. (A sopops with passwords or several providers still shows them.)
  const only = auth && !auth.password_email && auth.providers.length === 1 && auth.providers[0]?.redirects ? auth.providers[0].id : null;
  useEffect(() => {
    if (done) void go(safeNext(next), { replace: true });
    else if (only && !error) window.location.replace(signInHref(only, safeNext(next)));
  }, [done, only, error, next, go]);
  if (!auth || done || (only && !error)) return null;
  if (auth.first_account) return <Navigate to="/welcome" replace />;
  return (
    <Card title="Sign in to sopops">
      <p className="text-muted-foreground mb-5 text-sm leading-relaxed">Use the account or email your team uses for sopops.</p>
      {error && (
        <Banner tone="err" className="mb-4">
          {error.charAt(0).toUpperCase() + error.slice(1)}.
        </Banner>
      )}
      <SignInButtons auth={auth} next={safeNext(next)} />
      <p className="text-muted-foreground mt-5 border-t pt-4 text-center text-sm">
        New here?{" "}
        <Link to="/sign-up" className="text-primary underline-offset-2 hover:underline">
          Sign up
        </Link>
      </p>
    </Card>
  );
}
