// Signing in: the gate every page goes through, the sign-in buttons, the password and email forms, the "ask your team"
// page, and the user menu in the top bar (avatar, name, your teams and making one, change password, sign out). The
// first account on a fresh install is made on /welcome, and anyone else's on /sign-up (components/new-account.tsx).
import { useQueryClient } from "@tanstack/react-query";
import { Navigate, useLocation } from "@tanstack/react-router";
import { Check, KeyRound, LogOut, Mail, Plus } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import { type AuthState, type UserOut, call, sdk } from "@/lib/api";
import { signInHref, useAuth, useSignOut } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { Banner, ErrorBanner } from "./bits";
import { GitHubIcon, GoogleIcon } from "./brand-icon";
import { Logo } from "./logo";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "./ui/dropdown-menu";

/** Pages anyone can open: signing in, signing up, the first run, an emailed sign-in link, a link that sets a password, and invite and join links. */
const OPEN_PAGES = ["/sign-in", "/sign-up", "/welcome", "/sign-in/email", "/set-password", "/invite", "/join"];

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

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** After signing in: start over at `next`, with nothing cached from before. */
export function useSignedIn() {
  const qc = useQueryClient();
  return (next: string) => {
    qc.clear();
    window.location.assign(next.startsWith("/sign-in") ? "/" : next);
  };
}

function Or() {
  return (
    <div className="text-muted-foreground my-1 flex items-center gap-3 text-[12px]">
      <span className="h-px flex-1 bg-current opacity-25" /> or <span className="h-px flex-1 bg-current opacity-25" />
    </div>
  );
}

/** "Continue with GitHub", "Continue with Google": full-page links, since the provider's page comes next. */
export function RedirectButtons({ auth, next }: { auth: AuthState; next: string }) {
  return (
    <>
      {auth.providers
        .filter((p) => p.redirects)
        .map((p) => (
          <Button key={p.id} asChild variant="outline" className="h-10 justify-center gap-2.5 text-[14px]">
            <a href={signInHref(p.id, next)}>
              {PROVIDER_ICON[p.id]}
              Continue with {p.label}
            </a>
          </Button>
        ))}
    </>
  );
}

type Form = "password" | "link" | "forgot";

/**
 * The ways to sign in here: GitHub and Google buttons, then an email and password. With email set up, "Forgot
 * password?" and "Email me a sign-in link instead" swap the form for one that emails a link. `password={false}` leaves
 * the password form out (an invite for someone with no account yet, who makes one instead).
 */
export function SignInButtons({ auth, next, password = true }: { auth: AuthState; next: string; password?: boolean }) {
  const byPassword = password && auth.providers.some((p) => p.id === "password");
  const byEmail = auth.providers.some((p) => p.id === "email");
  const [form, setForm] = useState<Form>(byPassword ? "password" : "link");
  const redirects = auth.providers.some((p) => p.redirects);
  if (!redirects && !byPassword && !byEmail)
    return <p className="text-muted-foreground text-[13px]">No way to sign in is set up on this sopops yet. Ask whoever runs it.</p>;
  // An invite's token waits in this tab, and an emailed link may open in another: signing in accepts the invite anyway.
  const after = next === "/invite" ? "/" : next;
  const back = byPassword ? () => setForm("password") : undefined;
  return (
    <div className="grid gap-2.5">
      <RedirectButtons auth={auth} next={next} />
      {redirects && (byPassword || byEmail) && <Or />}
      {form === "password" && byPassword && <PasswordSignIn next={next} />}
      {form === "link" && byEmail && <EmailSignIn next={after} onBack={back} />}
      {form === "forgot" && <PasswordEmail signUp={false} onBack={() => setForm("password")} />}
      {form === "password" && byPassword && (
        <div className="text-muted-foreground mt-1 grid gap-1 text-center text-[13px]">
          {auth.password_email ? (
            <TextButton onClick={() => setForm("forgot")}>Forgot password?</TextButton>
          ) : (
            <span>Forgot your password? Ask an owner or admin for a reset link.</span>
          )}
          {byEmail && <TextButton onClick={() => setForm("link")}>Email me a sign-in link instead</TextButton>}
        </div>
      )}
    </div>
  );
}

function TextButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" className="hover:text-foreground underline-offset-2 hover:underline" onClick={onClick}>
      {children}
    </button>
  );
}

function PasswordSignIn({ next }: { next: string }) {
  const signedIn = useSignedIn();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const got = await call(sdk.passwordLogin({ body: { email: email.trim(), password, next } }));
      signedIn(got.next);
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  };
  return (
    <form
      className="grid gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <Label htmlFor="sign-in-email" className="text-[13px]">
        Email
      </Label>
      <Input
        id="sign-in-email"
        type="email"
        required
        autoComplete="username"
        className="h-10"
        placeholder="name@example.com"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      <Label htmlFor="sign-in-password" className="mt-1 text-[13px]">
        Password
      </Label>
      <Input
        id="sign-in-password"
        type="password"
        required
        autoComplete="current-password"
        className="h-10"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      {error && <Banner tone="err">{error}</Banner>}
      <Button type="submit" className="mt-1 h-10 text-[14px]" disabled={busy || !email.trim() || !password}>
        Sign in
      </Button>
    </form>
  );
}

/** Email a link that sets a password: "Forgot password?", or making an account at an allowed domain. */
export function PasswordEmail({ signUp, onBack }: { signUp: boolean; onBack: () => void }) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      setSent((await call(sdk.requestPasswordLink({ body: { email: email.trim() } }))).message);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  if (sent)
    return (
      <Banner tone="ok">
        <b>Check your email.</b> {sent} Didn't get it? Check spam, or{" "}
        <button type="button" className="underline underline-offset-2" onClick={onBack}>
          go back to sign in
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
      <p className="text-muted-foreground text-[13px] leading-relaxed">
        {signUp ? "We email you a link to set your password and make your account." : "We email you a link to set a new password."}
      </p>
      <Label htmlFor="password-email" className="text-[13px]">
        {signUp ? "Work email" : "Email"}
      </Label>
      <Input
        id="password-email"
        type="email"
        required
        autoComplete="email"
        className="h-10"
        placeholder="name@example.com"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      {error && <Banner tone="err">{error}</Banner>}
      <Button type="submit" className="h-10 gap-2.5 text-[14px]" disabled={busy || !email.trim()}>
        <Mail className="size-[18px]" /> {signUp ? "Email me a link" : "Email me a reset link"}
      </Button>
      <TextButton onClick={onBack}>
        <span className="text-muted-foreground text-[13px]">Back to sign in</span>
      </TextButton>
    </form>
  );
}

/** Ask for a sign-in link by email. The answer never says whether the address has an account. */
function EmailSignIn({ next, onBack }: { next: string; onBack?: () => void }) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      const got = await call(sdk.requestEmailLink({ body: { email: email.trim(), next } }));
      setSent(got.message);
    } catch (e) {
      setError(message(e));
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
      <Label htmlFor="sign-in-link-email" className="text-[13px]">
        Email
      </Label>
      <Input
        id="sign-in-link-email"
        type="email"
        required
        autoComplete="email"
        className="h-10"
        placeholder="name@example.com"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      {error && <Banner tone="err">{error}</Banner>}
      <Button type="submit" className="h-10 gap-2.5 text-[14px]" disabled={busy || !email.trim()}>
        <Mail className="size-[18px]" /> Email me a sign-in link
      </Button>
      {onBack && (
        <TextButton onClick={onBack}>
          <span className="text-muted-foreground text-[13px]">Sign in with a password instead</span>
        </TextButton>
      )}
    </form>
  );
}

/** A new password: at least 10 characters, and not the email address (the server says so if it is). */
export function NewPassword({ id, label = "Password", value, onChange }: { id: string; label?: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="grid gap-1">
      <Label htmlFor={id} className="text-[13px]">
        {label}
      </Label>
      <Input
        id={id}
        type="password"
        required
        minLength={10}
        maxLength={256}
        autoComplete="new-password"
        className="h-10"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <span className="text-muted-foreground text-[12px]">At least 10 characters.</span>
    </div>
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
    <span
      style={box}
      aria-hidden
      className={cn("bg-brand-soft text-foreground inline-flex shrink-0 items-center justify-center rounded-full text-[11px] font-semibold", className)}
    >
      {initials}
    </span>
  );
}

/** Signed in but in no org: say who to ask. */
function NoOrg({ auth }: { auth: AuthState }) {
  const signOut = useSignOut();
  return (
    <Card title="Ask your team for its join link">
      <p className="mb-3 text-[14px] leading-relaxed">
        You're signed in as <b>{auth.user?.email}</b>, but you're not on a team on this sopops yet.
      </p>
      <p className="text-muted-foreground mb-5 text-[13px] leading-relaxed">
        Ask an owner or admin for the team's join link, or to add this email in Settings, under Members. Then open the link, or sign in again, and you're in.
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
  const [changing, setChanging] = useState(false);
  const [creating, setCreating] = useState(false);
  const user = auth.user;
  if (!user) return null;
  const switchTo = async (orgId: string) => {
    try {
      const state = await call(sdk.switchOrg({ body: { org_id: orgId } }));
      qc.clear();
      window.location.assign(state.org?.workspace ? `/w/${encodeURIComponent(state.org.workspace)}` : "/");
    } catch (e) {
      toast.error(message(e));
    }
  };
  return (
    <>
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
          <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">{auth.orgs.length > 1 ? "Your teams" : "Your team"}</DropdownMenuLabel>
          {auth.orgs.map((o) => (
            <DropdownMenuItem key={o.id} onSelect={() => o.id !== auth.org?.id && void switchTo(o.id)} className="gap-2">
              <span className="flex-1 truncate">{o.name}</span>
              <span className="text-muted-foreground text-xs">{o.role}</span>
              {o.id === auth.org?.id ? <Check className="size-4" /> : <span className="size-4" />}
            </DropdownMenuItem>
          ))}
          {auth.org && (
            <DropdownMenuItem onSelect={() => setCreating(true)}>
              <Plus className="size-4" /> Create a team
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          {user.has_password && (
            <DropdownMenuItem onSelect={() => setChanging(true)}>
              <KeyRound className="size-4" /> Change password
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onSelect={() => void signOut()}>
            <LogOut className="size-4" /> Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={changing} onOpenChange={setChanging}>
        <DialogContent className="sm:max-w-[420px]">{changing && <ChangePassword onDone={() => setChanging(false)} />}</DialogContent>
      </Dialog>
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent className="sm:max-w-[420px]">{creating && <CreateTeam onDone={() => setCreating(false)} />}</DialogContent>
      </Dialog>
    </>
  );
}

/** A new team you own, with nothing in it yet. You switch to it; your other teams stay as they are. */
function CreateTeam({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const made = await call(sdk.createTeam({ body: { name: name.trim() } }));
      qc.clear();
      window.location.assign(`/w/${encodeURIComponent(made.workspace)}`);
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  };
  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <DialogHeader>
        <DialogTitle>Create a team</DialogTitle>
        <DialogDescription>A new team starts empty, and you own it. You stay on your other teams, and switch between them from this menu.</DialogDescription>
      </DialogHeader>
      <div className="grid gap-1">
        <Label htmlFor="team-name" className="text-[13px]">
          What's your team called?
        </Label>
        <Input id="team-name" autoFocus className="h-10" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      {error && <Banner tone="err">{error}</Banner>}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={busy || !name.trim()}>
          Create team
        </Button>
      </DialogFooter>
    </form>
  );
}

/** Change your own password. Your other sessions end; this one stays. */
function ChangePassword({ onDone }: { onDone: () => void }) {
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await call(sdk.changePassword({ body: { current_password: current, password } }));
      toast.success("Password changed. You're signed out everywhere else.");
      onDone();
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  };
  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <DialogHeader>
        <DialogTitle>Change your password</DialogTitle>
        <DialogDescription>This signs you out everywhere else.</DialogDescription>
      </DialogHeader>
      <div className="grid gap-1">
        <Label htmlFor="current-password" className="text-[13px]">
          Current password
        </Label>
        <Input
          id="current-password"
          type="password"
          required
          autoComplete="current-password"
          className="h-10"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
      </div>
      <NewPassword id="new-password" label="New password" value={password} onChange={setPassword} />
      {error && <Banner tone="err">{error}</Banner>}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={busy || !current || password.length < 10}>
          Change password
        </Button>
      </DialogFooter>
    </form>
  );
}
