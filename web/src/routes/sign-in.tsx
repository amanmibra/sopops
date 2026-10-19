import { Link, Navigate, createFileRoute } from "@tanstack/react-router";
import { useEffect } from "react";
import { Card, SignInButtons } from "@/components/account";
import { Banner } from "@/components/bits";
import { useGo } from "@/components/href-link";
import { useAuth } from "@/lib/auth";

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
  useEffect(() => {
    if (done) void go(safeNext(next), { replace: true });
  }, [done, next, go]);
  if (!auth || done) return null;
  if (auth.first_account) return <Navigate to="/welcome" replace />;
  return (
    <Card title="Sign in to sopops">
      <p className="text-muted-foreground mb-5 text-[13px] leading-relaxed">Use the account or email your team uses for sopops.</p>
      {error && (
        <Banner tone="err" className="mb-4">
          {error.charAt(0).toUpperCase() + error.slice(1)}.
        </Banner>
      )}
      <SignInButtons auth={auth} next={safeNext(next)} />
      <p className="text-muted-foreground mt-5 border-t pt-4 text-center text-[13px]">
        New here?{" "}
        <Link to="/sign-up" className="text-primary underline-offset-2 hover:underline">
          Sign up
        </Link>
      </p>
    </Card>
  );
}
