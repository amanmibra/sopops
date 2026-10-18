// Signing in: the gate every page goes through, the sign-in buttons and the email form, the "ask an owner" page, and the
// user menu in the top bar (avatar, name, org, sign out).
import { useQueryClient } from "@tanstack/react-query";
import { Navigate, useLocation } from "@tanstack/react-router";
import { Check, LogOut, Mail } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import { type AuthState, type UserOut, call, sdk } from "@/lib/api";
import { signInHref, useAuth, useSignOut } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { Banner, ErrorBanner } from "./bits";
import { GitHubIcon, GoogleIcon } from "./brand-icon";
import { Logo } from "./logo";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";

/** Pages anyone can open: signing in, an emailed sign-in link, and an invite link. */
const OPEN_PAGES = ["/sign-in", "/sign-in/email", "/invite"];

/**
 * With sign-in on: send people who aren't signed in to the sign-in page, and people in no org to "ask an owner".
 * With it off, every page shows as it always did.
 */
export function AuthGate({ children }: { children: (auth: AuthState) => ReactNode }) {
  const auth = useAuth();
  const { pathname, searchStr } = useLocation();
  if (auth.isPending) return <main className="text-muted-foreground px-8 py-6">Loading…</main>;
  if (auth.isError)
    return (
      <main className="px-8 py-6">
        <ErrorBanner error={auth.error} lead="sopops didn't answer:" />
      </main>
    );
  const a = auth.data;
  const open = OPEN_PAGES.includes(pathname);
  if (a.required && !a.user && !open) return <Navigate to="/sign-in" search={{ next: pathname + searchStr }} replace />;
  if (a.required && a.user && !a.org && !open) return <NoOrg auth={a} />;
  return children(a);
}

/** A centred card for the pages outside the app: signing in, invites, no org. */
export function Card({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <main className="flex min-h-screen items-start justify-center px-4 pt-[12vh] pb-10">
      <div className="bg-card w-full max-w-[420px] rounded-xl border px-6 py-7 shadow-sm">
        <div className="mb-5 flex items-center gap-2 text-[15px] font-semibold tracking-tight">
          <Logo /> sopops
        </div>
        <h1 className="mb-2 text-[20px] leading-tight font-semibold tracking-tight">{title}</h1>
        {children}
      </div>
    </main>
  );
}

const PROVIDER_ICON: Record<string, ReactNode> = { github: <GitHubIcon size={18} />, google: <GoogleIcon size={18} /> };

/**
 * "Continue with GitHub", "Continue with Google": full-page links, since the provider's page comes next. Then, with email
 * sign-in on, a box for an address and "Email me a sign-in link".
 */
export function SignInButtons({ auth, next }: { auth: AuthState; next: string }) {
  if (!auth.providers.length)
    return (
      <p className="text-muted-foreground text-[13px]">
        No way to sign in is set up on this sopops yet. Ask whoever runs it to set up GitHub, Google or email sign-in.
      </p>
    );
  const redirects = auth.providers.filter((p) => p.redirects);
  const byEmail = auth.providers.some((p) => p.id === "email");
  return (
    <div className="grid gap-2.5">
      {redirects.map((p) => (
        <Button key={p.id} asChild variant="outline" className="h-10 justify-center gap-2.5 text-[14px]">
          <a href={signInHref(p.id, next)}>
            {PROVIDER_ICON[p.id]}
            Continue with {p.label}
          </a>
        </Button>
      ))}
      {byEmail && redirects.length > 0 && (
        <div className="text-muted-foreground my-1 flex items-center gap-3 text-[12px]">
          <span className="h-px flex-1 bg-current opacity-25" /> or <span className="h-px flex-1 bg-current opacity-25" />
        </div>
      )}
      {byEmail && <EmailSignIn next={next} />}
    </div>
  );
}

/** Ask for a sign-in link by email. The answer never says whether the address has an account. */
function EmailSignIn({ next }: { next: string }) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // An invite's token waits in this tab, and the link may open in another: signing in accepts the invite anyway.
  const after = next === "/invite" ? "/" : next;
  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      const got = await call(sdk.requestEmailLink({ body: { email: email.trim(), next: after } }));
      setSent(got.message);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  if (sent)
    return (
      <Banner tone="ok">
        <b>Check your email.</b> {sent} Didn't get it? Check spam, or{" "}
        <button type="button" className="underline underline-offset-2" onClick={() => setSent(null)}>
          try again
        </button>
        .
      </Banner>
    );
  return (
    <form
      className="grid gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      <Label htmlFor="sign-in-email" className="text-[13px]">
        Email
      </Label>
      <Input id="sign-in-email" type="email" required autoComplete="email" className="h-10" placeholder="name@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
      {error && <Banner tone="err">{error}</Banner>}
      <Button type="submit" className="h-10 gap-2.5 text-[14px]" disabled={busy || !email.trim()}>
        <Mail className="size-[18px]" /> Email me a sign-in link
      </Button>
    </form>
  );
}

export function Avatar({ user, size = 28, className }: { user: Pick<UserOut, "name" | "email" | "avatar_url">; size?: number; className?: string }) {
  const initials = (user.name || user.email)
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
  const box = { width: size, height: size };
  if (user.avatar_url)
    return <img src={user.avatar_url} alt="" style={box} referrerPolicy="no-referrer" className={cn("shrink-0 rounded-full border object-cover", className)} />;
  return (
    <span style={box} aria-hidden className={cn("bg-brand-soft text-foreground inline-flex shrink-0 items-center justify-center rounded-full text-[11px] font-semibold", className)}>
      {initials}
    </span>
  );
}

/** Signed in but in no org: say who to ask. */
function NoOrg({ auth }: { auth: AuthState }) {
  const signOut = useSignOut();
  return (
    <Card title="Ask an owner to invite you">
      <p className="mb-3 text-[14px] leading-relaxed">
        You're signed in as <b>{auth.user?.email}</b>, but you're not in an org on this sopops yet.
      </p>
      <p className="text-muted-foreground mb-5 text-[13px] leading-relaxed">
        Ask an owner or admin to add this email address in Settings, Members. Then sign in again, or open the link they send you, and you're in.
      </p>
      <Button variant="outline" onClick={() => void signOut()}>
        <LogOut /> Sign out
      </Button>
    </Card>
  );
}

/** The top bar's avatar: who you are, the org you're working in (and the others you're in), and Sign out. */
export function UserMenu({ auth }: { auth: AuthState }) {
  const qc = useQueryClient();
  const signOut = useSignOut();
  const user = auth.user;
  if (!user) return null;
  const switchTo = async (orgId: string) => {
    try {
      await call(sdk.switchOrg({ body: { org_id: orgId } }));
      qc.clear();
      window.location.assign("/");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" className="rounded-full" aria-label={`Signed in as ${user.name || user.email}`} title={user.email}>
          <Avatar user={user} size={26} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-60">
        <DropdownMenuLabel className="flex items-center gap-2.5 font-normal">
          <Avatar user={user} size={32} />
          <span className="min-w-0">
            <span className="block truncate font-semibold">{user.name || user.email}</span>
            <span className="text-muted-foreground block truncate text-xs">{user.email}</span>
          </span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">{auth.orgs.length > 1 ? "Your orgs" : "Your org"}</DropdownMenuLabel>
        {auth.orgs.map((o) => (
          <DropdownMenuItem key={o.id} onSelect={() => o.id !== auth.org?.id && void switchTo(o.id)} className="gap-2">
            <span className="flex-1 truncate">{o.name}</span>
            <span className="text-muted-foreground text-xs">{o.role}</span>
            {o.id === auth.org?.id ? <Check className="size-4" /> : <span className="size-4" />}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void signOut()}>
          <LogOut className="size-4" /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
