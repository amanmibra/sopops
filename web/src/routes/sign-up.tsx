import { Navigate, createFileRoute } from "@tanstack/react-router";
import { Card, PasswordEmail } from "@/components/account";
import { NewAccount } from "@/components/new-account";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

export const Route = createFileRoute("/sign-up")({ component: SignUp });

/**
 * "New here? Sign up" on the sign-in page comes here. With SOPOPS_OPEN_SIGNUP on: the first run's steps 1 to 3 (name,
 * email and password, team), then the team's setup. Off: how to join a team, which is with its join link (and, at
 * SOPOPS_ALLOWED_EMAIL_DOMAINS, by email). On a fresh install it's the first run.
 */
function SignUp() {
  const auth = useAuth().data;
  if (!auth) return null;
  if (!auth.required || auth.user) return <Navigate to="/" replace />;
  if (auth.first_account) return <Navigate to="/welcome" replace />;
  if (auth.open_signup) return <NewAccount auth={auth} start="name" signUp />;
  return (
    <Card title="Join your team with its join link">
      <p className="text-muted-foreground mb-5 text-sm leading-relaxed">
        To join a team, open the join link someone on it sent you. Don't have one? Ask a team owner or admin for it.
      </p>
      {auth.sign_up && (
        <div className="mb-5">
          <PasswordEmail signUp onBack={() => window.location.assign("/sign-in")} />
        </div>
      )}
      {!auth.sign_up && (
        <Button asChild variant="outline">
          <a href="/sign-in">Back to sign in</a>
        </Button>
      )}
    </Card>
  );
}
