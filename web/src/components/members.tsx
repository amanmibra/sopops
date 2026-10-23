// Settings → Members: the team's people and roles, its join link, adding people by email (they join now, or when they first sign in),
// reset links for passwords, who's invited and not signed in yet, and the email domains whose people join by signing in.
// Members do the work; owners and admins also change the team. Owner differs from admin in one thing only:
// making and removing owners.
//
// Where the teams aren't sopops's to hand out (every way of signing in here owns them), the page is the same page
// with fewer parts: one invite form, the list of who's waiting, and nothing that depends on a sopops link. The
// invite is sent by whoever keeps the teams, so there's no link to copy, nothing to resend, and no join link or
// email domain for sopops to offer. The server decides the same thing from the team itself and does the same.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Copy, Globe, Link2, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { type AuthState, type InviteCreated, type JoinLinkCreated, type InviteOut, type MemberOut, type PasswordLinkCreated, type PersonAdded, call, sdk } from "@/lib/api";
import { when } from "@/lib/kinds";
import { Avatar } from "./account";
import { Banner, ConfirmButton, ErrorBanner, Note, Panel, Tag } from "./bits";
import { CopyLink, RoleSelect } from "./setup";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Textarea } from "./ui/textarea";

type Role = MemberOut["role"];

const ROLES: { role: Role; label: string; help: string }[] = [
  { role: "member", label: "Member", help: "Writes SOPs, publishes releases, reviews calls and makes tokens." },
  { role: "admin", label: "Admin", help: "All of that, plus people, invites, model keys, backfills and the team's settings." },
  { role: "owner", label: "Owner", help: "The same as an admin, and the only role that can make or remove an owner." },
];

const mq = {
  org: () => ({ queryKey: ["org"], queryFn: () => call(sdk.getOrg()) }),
  members: () => ({ queryKey: ["org", "members"], queryFn: () => call(sdk.listMembers()) }),
  invites: () => ({ queryKey: ["org", "invites"], queryFn: () => call(sdk.listInvites()) }),
};

const fail = (e: unknown) => toast.error(e instanceof Error ? e.message : String(e));

/** Whether the teams are kept outside sopops: every way of signing in here owns them, so none of sopops's own ways
 *  in (a join link, an email domain, an invite link to copy) exists to offer. */
const teamsElsewhere = (auth: AuthState) => auth.providers.length > 0 && auth.providers.every((p) => p.owns_teams);

export function MembersPanel({ auth }: { auth: AuthState }) {
  const role = auth.org?.role;
  const manager = role === "owner" || role === "admin";
  const elsewhere = teamsElsewhere(auth);
  const org = useQuery(mq.org());
  return (
    <>
      {org.isError && <ErrorBanner error={org.error} lead="The team didn't load:" />}
      {manager && !elsewhere && <ShareJoinLink orgName={auth.org?.name ?? ""} />}
      {manager &&
        (elsewhere ? (
          <InviteSomeone orgName={auth.org?.name ?? ""} />
        ) : (
          <AddPeople sendsEmail={org.data?.sends_email ?? false} orgName={auth.org?.name ?? ""} />
        ))}
      <People auth={auth} manager={manager} elsewhere={elsewhere} />
      {manager && <Pending sendsEmail={org.data?.sends_email ?? false} elsewhere={elsewhere} />}
      {manager && !elsewhere && org.data && <Domains domains={org.data.allowed_domains} orgName={org.data.name} />}
      {!manager && <Note>Owners and admins add people and change roles.</Note>}
    </>
  );
}

function People({ auth, manager, elsewhere }: { auth: AuthState; manager: boolean; elsewhere: boolean }) {
  const qc = useQueryClient();
  const list = useQuery(mq.members());
  const [reset, setReset] = useState<PasswordLinkCreated | null>(null);
  const isOwner = auth.org?.role === "owner";
  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: ["org"] }), qc.invalidateQueries({ queryKey: ["auth"] })]);
  const setRole = async (m: MemberOut, r: Role) => {
    try {
      await call(sdk.setMemberRole({ path: { user_id: m.user_id }, body: { role: r } }));
      await refresh();
      toast.success(`${m.name || m.email} is now ${r === "member" ? "a member" : `an ${r}`}.`);
    } catch (e) {
      fail(e);
    }
  };
  const remove = async (m: MemberOut) => {
    try {
      await call(sdk.removeMember({ path: { user_id: m.user_id } }));
      await refresh();
      toast.success(`${m.name || m.email} is out of the org.`);
    } catch (e) {
      fail(e);
    }
  };
  const resetLink = async (m: MemberOut) => {
    try {
      setReset(await call(sdk.createPasswordLink({ path: { user_id: m.user_id } })));
    } catch (e) {
      fail(e);
    }
  };
  // Only an owner changes an owner, or makes one.
  const canChange = (m: MemberOut) => manager && (isOwner || m.role !== "owner");
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <Users className="size-4" /> People in {auth.org?.name}
        </span>
      }
    >
      {list.isError && <ErrorBanner error={list.error} lead="The members didn't load:" />}
      <ul className="divide-y">
        {(list.data ?? []).map((m) => (
          <li key={m.user_id} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2">
            <Avatar user={m} size={30} />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">
                {m.name || m.email}
                {m.user_id === auth.user?.id && <span className="text-muted-foreground font-normal"> (you)</span>}
              </span>
              <span className="text-muted-foreground block truncate text-sm">
                {m.email} · joined {when(m.joined_at)}
              </span>
            </span>
            {canChange(m) ? (
              <Select value={m.role} onValueChange={(v) => void setRole(m, v as Role)}>
                <SelectTrigger size="sm" className="w-28" aria-label={`Role of ${m.email}`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROLES.filter((r) => isOwner || r.role !== "owner").map((r) => (
                    <SelectItem key={r.role} value={r.role}>
                      {r.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Tag>{m.role}</Tag>
            )}
            {canChange(m) && m.user_id !== auth.user?.id && (
              <>
                {!elsewhere && (
                  <Button size="sm" variant="ghost" onClick={() => void resetLink(m)}>
                    Reset password
                  </Button>
                )}
                <ConfirmButton size="sm" variant="ghost" className="text-err" confirm="Click again to remove" onConfirm={() => void remove(m)}>
                  Remove
                </ConfirmButton>
              </>
            )}
          </li>
        ))}
      </ul>
      {reset && (
        <Banner tone="ok" className="mt-3">
          <LinkBox
            email={reset.email}
            link={reset.link}
            what="the reset link"
            note="It sets their password and signs them out everywhere. It works once, for an hour, and isn't shown again."
          />
        </Banner>
      )}
      <ul className="text-muted-foreground mt-3 grid gap-0.5 text-sm">
        {ROLES.map((r) => (
          <li key={r.role}>
            <b className="text-foreground font-medium">{r.label}</b>: {r.help}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

/** Links shown once: to copy when sopops doesn't email them (or the email didn't go), and password reset links. */
function LinkBox({ email, link, what = "the invite link", note = "It isn't shown again." }: { email: string; link: string; what?: string; note?: string }) {
  const copy = () => navigator.clipboard.writeText(link).then(() => toast.success("Copied."), fail);
  return (
    <div className="mt-2">
      <div className="text-sm">
        Send this link to {email}. {note}
      </div>
      <div className="bg-card mt-1 flex items-center gap-2 rounded-md border px-2 py-2">
        <code className="flex-1 font-mono text-sm break-all">{link}</code>
        <Button size="icon-sm" variant="ghost" aria-label={`Copy ${what} for ${email}`} onClick={() => void copy()}>
          <Copy />
        </Button>
      </div>
    </div>
  );
}

const asRole = (r: Role) => (r === "member" ? "a member" : `an ${r}`);
const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: "long", day: "numeric" });

/**
 * The team's join link: one link, not tied to an email, that anyone can open to join with its role. It's shown once,
 * when made (only a hash is kept), so "Make a new link" is how to copy it again; the old one stops working.
 */
function ShareJoinLink({ orgName }: { orgName: string }) {
  const qc = useQueryClient();
  const current = useQuery({ queryKey: ["org", "join-link"], queryFn: () => call(sdk.getJoinLink()) });
  const [role, setRole] = useState<"member" | "admin">("member");
  const [made, setMade] = useState<JoinLinkCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const live = current.data ?? null;
  const make = async () => {
    setBusy(true);
    try {
      setMade(await call(sdk.createJoinLink({ body: { role, days: 7 } })));
      await qc.invalidateQueries({ queryKey: ["org", "join-link"] });
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  const turnOff = async () => {
    try {
      await call(sdk.deleteJoinLink());
      setMade(null);
      await qc.invalidateQueries({ queryKey: ["org", "join-link"] });
      toast.success("The join link is off.");
    } catch (e) {
      fail(e);
    }
  };
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <Link2 className="size-4" /> Share a join link
        </span>
      }
    >
      <p className="text-muted-foreground mb-3 max-w-prose text-sm leading-relaxed">
        Anyone who opens the link can make an account, or sign in, and join {orgName}. Send it however your team talks.
      </p>
      {current.isError && <ErrorBanner error={current.error} lead="The join link didn't load:" />}
      {live && !made && (
        <p className="mb-3 text-sm">
          A link is on. People join as {asRole(live.role as Role)}, until {day(live.expires_at)}.{" "}
          {live.joined > 0 && <>{live.joined === 1 ? "1 person has" : `${live.joined} people have`} joined with it. </>}
          <span className="text-muted-foreground">It's shown only once, so make a new one to copy it again.</span>
        </p>
      )}
      {made && (
        <div className="mb-3 grid gap-2">
          <CopyLink link={made.link} label="Copy the join link" />
          <span className="text-muted-foreground text-sm">
            People join as {asRole(made.role as Role)}. It works until {day(made.expires_at)}, and isn't shown again.
          </span>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor="join-link-role" className="text-sm">
          People join as
        </Label>
        <RoleSelect id="join-link-role" value={role} onChange={setRole} />
        <Button size="sm" disabled={busy} onClick={() => void make()}>
          {live ? "Make a new link" : "Make a join link"}
        </Button>
        {live && (
          <ConfirmButton size="sm" variant="ghost" className="text-err" confirm="Click again: the link stops working" onConfirm={() => void turnOff()}>
            Turn off
          </ConfirmButton>
        )}
      </div>
      {live && <Note className="mt-2 block">Making a new link stops the old one.</Note>}
    </Panel>
  );
}

/**
 * One address at a time, for a team that isn't sopops's to hand out: the invite is sent by whoever keeps the teams,
 * so there's no link here to copy and nothing to resend. Whoever accepts is on the team the moment they sign in.
 */
function InviteSomeone({ orgName }: { orgName: string }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [busy, setBusy] = useState(false);
  const invite = async () => {
    setBusy(true);
    try {
      await call(sdk.createInvite({ body: { email: email.trim(), role } }));
      setEmail("");
      await qc.invalidateQueries({ queryKey: ["org", "invites"] });
      toast.success(`Invited ${email.trim()}. They'll get an email with a link to join.`);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <UserPlus className="size-4" /> Invite someone
        </span>
      }
    >
      <p className="text-muted-foreground mb-3 max-w-prose text-sm leading-relaxed">
        They get an email with a link to join {orgName}. Once they've used it and signed in, they're here too, with the role you pick.
      </p>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void invite();
        }}
      >
        <div className="grid max-w-[320px] flex-1 gap-1">
          <Label htmlFor="invite-email" className="text-sm">
            Email
          </Label>
          <Input id="invite-email" type="email" className="h-9 text-sm" placeholder="ana@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="grid gap-1">
          <Label className="text-sm">Role</Label>
          <Select value={role} onValueChange={(v) => setRole(v as Role)}>
            <SelectTrigger size="sm" className="w-28" aria-label="Role">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ROLES.filter((r) => r.role !== "owner").map((r) => (
                <SelectItem key={r.role} value={r.role}>
                  {r.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button type="submit" size="sm" disabled={busy || !email.trim()}>
          Invite
        </Button>
      </form>
    </Panel>
  );
}

function AddPeople({ sendsEmail, orgName }: { sendsEmail: boolean; orgName: string }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [done, setDone] = useState<{ role: Role; people: PersonAdded[] }>({ role: "member", people: [] });
  const [busy, setBusy] = useState(false);
  const emails = text
    .split(/[\s,;]+/)
    .map((e) => e.trim())
    .filter(Boolean);
  const add = async () => {
    setBusy(true);
    try {
      const got = await call(sdk.addMembers({ body: { emails, role } }));
      setDone({ role, people: got });
      setText("");
      await Promise.all([qc.invalidateQueries({ queryKey: ["org"] }), qc.invalidateQueries({ queryKey: ["auth"] })]);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <UserPlus className="size-4" /> Add people by email
        </span>
      }
    >
      <p className="text-muted-foreground mb-3 max-w-prose text-sm leading-relaxed">
        Anyone who already has an account joins {orgName} right away. Everyone else gets an invite link. They open it and set a password, or sign in another way
        with that email, and they're in.{" "}
        {sendsEmail ? "sopops emails them the link." : "sopops doesn't send email here, so you get the link to send them yourself."}
      </p>
      <form
        className="grid gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void add();
        }}
      >
        <div className="grid max-w-[480px] gap-1">
          <Label htmlFor="add-emails" className="text-sm">
            Emails, separated by commas or new lines
          </Label>
          <Textarea id="add-emails" rows={2} className="text-sm" placeholder="ana@example.com, bo@example.com" value={text} onChange={(e) => setText(e.target.value)} />
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1">
            <Label className="text-sm">Role</Label>
            <Select value={role} onValueChange={(v) => setRole(v as Role)}>
              <SelectTrigger size="sm" className="w-28" aria-label="Role">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLES.filter((r) => r.role !== "owner").map((r) => (
                  <SelectItem key={r.role} value={r.role}>
                    {r.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button type="submit" size="sm" disabled={busy || emails.length === 0}>
            {emails.length > 1 ? `Add ${emails.length} people` : "Add"}
          </Button>
        </div>
      </form>
      {done.people.length > 0 && (
        <Banner tone="ok" className="mt-4">
          <ul className="grid gap-2">
            {done.people.map((p) => (
              <li key={p.email}>
                {p.status === "added" && (
                  <>
                    <b>{p.email}</b> joined as {asRole(done.role)}.
                  </>
                )}
                {p.status === "already" && (
                  <>
                    <b>{p.email}</b> is already in {orgName}.
                  </>
                )}
                {p.status === "invited" && p.invite && (
                  <>
                    <b>{p.email}</b> is invited as {asRole(p.invite.role)}.{" "}
                    {p.invite.emailed ? "We emailed them the link." : sendsEmail ? "The email didn't go." : "They join when they open the link."}
                    {!p.invite.emailed && <LinkBox email={p.email} link={p.invite.link} />}
                  </>
                )}
              </li>
            ))}
          </ul>
        </Banner>
      )}
    </Panel>
  );
}

/**
 * Invited, not signed in yet: send the invite again or cancel it. Without email, "Copy link" copies a new link instead:
 * links are stored hashed, so the old one can't be shown again, and it stops working.
 */
function Pending({ sendsEmail, elsewhere }: { sendsEmail: boolean; elsewhere: boolean }) {
  const qc = useQueryClient();
  const list = useQuery(mq.invites());
  const [fresh, setFresh] = useState<InviteCreated | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["org", "invites"] });
  const resend = async (i: InviteOut) => {
    try {
      const got = await call(sdk.resendInvite({ path: { id: i.id } }));
      await refresh();
      if (got.emailed) {
        setFresh(null);
        toast.success(`Sent again to ${i.email}.`);
      } else {
        setFresh(got);
        if (sendsEmail) toast.error(`The email to ${i.email} didn't go. Copy the new link instead.`);
      }
    } catch (e) {
      fail(e);
    }
  };
  const copyLink = async (i: InviteOut) => {
    try {
      const got = await call(sdk.resendInvite({ path: { id: i.id } }));
      await refresh();
      setFresh(got);
      await navigator.clipboard.writeText(got.link);
      toast.success(`Copied a new link for ${i.email}. The old link no longer works.`);
    } catch (e) {
      fail(e);
    }
  };
  const cancel = async (i: InviteOut) => {
    try {
      await call(sdk.revokeInvite({ path: { id: i.id } }));
      await refresh();
      if (fresh?.id === i.id) setFresh(null);
      toast.success(`Invite for ${i.email} cancelled. Its link stops working now.`);
    } catch (e) {
      fail(e);
    }
  };
  const invites = list.data ?? [];
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <Clock className="size-4" /> Invited, not signed in yet
        </span>
      }
    >
      {list.isError && <ErrorBanner error={list.error} lead="The invites didn't load:" />}
      {invites.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          {elsewhere ? "Nobody is waiting. People you invite show up here until they join." : "Nobody is waiting. People you add who don't have an account yet show up here."}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <tbody>
              {invites.map((i) => (
                <tr key={i.id}>
                  <td className="border-b py-2 pr-2">{i.email}</td>
                  <td className="border-b py-2 pr-2">
                    <Tag>{i.role}</Tag>
                  </td>
                  <td className="text-muted-foreground border-b py-2 pr-2">invited {when(i.created_at)}</td>
                  <td className="border-b py-2 text-right whitespace-nowrap">
                    {elsewhere ? null : sendsEmail ? (
                      <Button size="sm" variant="ghost" onClick={() => void resend(i)}>
                        Resend
                      </Button>
                    ) : (
                      <Button size="sm" variant="ghost" onClick={() => void copyLink(i)}>
                        <Copy /> Copy link
                      </Button>
                    )}
                    <ConfirmButton size="sm" variant="ghost" className="text-err" confirm="Click again to cancel" onConfirm={() => void cancel(i)}>
                      Cancel
                    </ConfirmButton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {fresh && (
        <Banner tone="ok" className="mt-3">
          <LinkBox email={fresh.email} link={fresh.link} />
          <div className="text-muted-foreground mt-1 text-sm">The old link stops working. This one works for 7 days.</div>
        </Banner>
      )}
    </Panel>
  );
}

function Domains({ domains, orgName }: { domains: string[]; orgName: string }) {
  const qc = useQueryClient();
  const [text, setText] = useState(domains.join("\n"));
  const [busy, setBusy] = useState(false);
  const parsed = text
    .split(/[\s,]+/)
    .map((d) => d.trim())
    .filter(Boolean);
  const changed = parsed.join("\n") !== domains.join("\n");
  const save = async () => {
    setBusy(true);
    try {
      const got = await call(sdk.updateOrg({ body: { allowed_domains: parsed } }));
      setText(got.allowed_domains.join("\n"));
      await qc.invalidateQueries({ queryKey: ["org"] });
      toast.success(got.allowed_domains.length ? "Saved. People from these domains join when they sign in." : "Saved. People join by invite only.");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <Globe className="size-4" /> Let your company's email domains join
        </span>
      }
    >
      <p className="text-muted-foreground mb-3 max-w-prose text-sm leading-relaxed">
        Anyone who signs in with a verified email at one of these domains joins {orgName} as a member, with no invite. Leave it empty to let people in by invite
        only.
      </p>
      <Label htmlFor="org-domains" className="mb-1 text-sm">
        Email domains, one per line
      </Label>
      <Textarea
        id="org-domains"
        className="mb-3 max-w-[360px] font-mono text-sm"
        rows={3}
        placeholder="example.com"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <Button size="sm" disabled={busy || !changed} onClick={() => void save()}>
        Save domains
      </Button>
    </Panel>
  );
}
