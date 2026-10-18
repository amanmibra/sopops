// Settings → Members: the org's people and roles, adding people by email (they join now, or when they first sign in),
// who's invited and not signed in yet, and the email domains whose people join by signing in. Owners and admins change
// things; members see who's here.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Copy, Globe, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { type AuthState, type InviteCreated, type InviteOut, type MemberOut, type PersonAdded, call, sdk } from "@/lib/api";
import { when } from "@/lib/kinds";
import { Avatar } from "./account";
import { Banner, ConfirmButton, ErrorBanner, Note, Panel, Tag } from "./bits";
import { Button } from "./ui/button";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Textarea } from "./ui/textarea";

type Role = MemberOut["role"];

const ROLES: { role: Role; label: string; help: string }[] = [
  { role: "member", label: "Member", help: "Uses everything in sopops." },
  { role: "admin", label: "Admin", help: "Also adds and removes people, changes roles and sets the org's email domains." },
  { role: "owner", label: "Owner", help: "Also makes and removes owners." },
];

const mq = {
  org: () => ({ queryKey: ["org"], queryFn: () => call(sdk.getOrg()) }),
  members: () => ({ queryKey: ["org", "members"], queryFn: () => call(sdk.listMembers()) }),
  invites: () => ({ queryKey: ["org", "invites"], queryFn: () => call(sdk.listInvites()) }),
};

const fail = (e: unknown) => toast.error(e instanceof Error ? e.message : String(e));

export function MembersPanel({ auth }: { auth: AuthState }) {
  const role = auth.org?.role;
  const manager = role === "owner" || role === "admin";
  const org = useQuery(mq.org());
  return (
    <>
      {org.isError && <ErrorBanner error={org.error} lead="The org didn't load:" />}
      {manager && <AddPeople sendsEmail={org.data?.sends_email ?? false} orgName={auth.org?.name ?? ""} />}
      <People auth={auth} manager={manager} />
      {manager && <Pending sendsEmail={org.data?.sends_email ?? false} />}
      {manager && org.data && <Domains domains={org.data.allowed_domains} orgName={org.data.name} />}
      {!manager && <Note>Owners and admins add people and change roles.</Note>}
    </>
  );
}

function People({ auth, manager }: { auth: AuthState; manager: boolean }) {
  const qc = useQueryClient();
  const list = useQuery(mq.members());
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
          <li key={m.user_id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2">
            <Avatar user={m} size={30} />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">
                {m.name || m.email}
                {m.user_id === auth.user?.id && <span className="text-muted-foreground font-normal"> (you)</span>}
              </span>
              <span className="text-muted-foreground block truncate text-[13px]">
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
              <ConfirmButton size="sm" variant="ghost" className="text-err" confirm="Click again to remove" onConfirm={() => void remove(m)}>
                Remove
              </ConfirmButton>
            )}
          </li>
        ))}
      </ul>
      <ul className="text-muted-foreground mt-3 grid gap-0.5 text-[13px]">
        {ROLES.map((r) => (
          <li key={r.role}>
            <b className="text-foreground font-medium">{r.label}</b>: {r.help}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

/** Links shown once: to copy when sopops doesn't email them (or the email didn't go). */
function LinkBox({ email, link }: { email: string; link: string }) {
  const copy = () => navigator.clipboard.writeText(link).then(() => toast.success("Copied."));
  return (
    <div className="mt-1.5">
      <div className="text-[13px]">Send this link to {email}. It isn't shown again.</div>
      <div className="bg-card mt-1 flex items-center gap-2 rounded-md border px-2.5 py-1.5">
        <code className="flex-1 font-mono text-[13px] break-all">{link}</code>
        <Button size="icon-sm" variant="ghost" aria-label={`Copy the invite link for ${email}`} onClick={() => void copy()}>
          <Copy />
        </Button>
      </div>
    </div>
  );
}

const asRole = (r: Role) => (r === "member" ? "a member" : `an ${r}`);

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
          <UserPlus className="size-4" /> Add people
        </span>
      }
    >
      <p className="text-muted-foreground mb-3 max-w-prose text-[13px] leading-relaxed">
        Anyone who already has an account joins {orgName} right away. Everyone else joins the moment they sign in with that email, with GitHub, Google or
        an email link.{" "}
        {sendsEmail ? "sopops emails them an invite." : "sopops doesn't send email here, so you get a link to send them yourself."}
      </p>
      <form
        className="grid gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void add();
        }}
      >
        <div className="grid max-w-[480px] gap-1">
          <Label htmlFor="add-emails" className="text-[13px]">
            Emails, separated by commas or new lines
          </Label>
          <Textarea id="add-emails" rows={2} className="text-[13px]" placeholder="ana@example.com, bo@example.com" value={text} onChange={(e) => setText(e.target.value)} />
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1">
            <Label className="text-[13px]">Role</Label>
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
                    {p.invite.emailed ? "We emailed them." : sendsEmail ? "The email didn't go." : "They join when they sign in."}
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

/** Invited, not signed in yet: send the invite again (a new link) or cancel it. */
function Pending({ sendsEmail }: { sendsEmail: boolean }) {
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
        <p className="text-muted-foreground text-[13px]">Nobody is waiting. People you add who don't have an account yet show up here.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <tbody>
              {invites.map((i) => (
                <tr key={i.id}>
                  <td className="border-b py-1.5 pr-2">{i.email}</td>
                  <td className="border-b py-1.5 pr-2">
                    <Tag>{i.role}</Tag>
                  </td>
                  <td className="text-muted-foreground border-b py-1.5 pr-2">invited {when(i.created_at)}</td>
                  <td className="border-b py-1.5 text-right whitespace-nowrap">
                    <Button size="sm" variant="ghost" onClick={() => void resend(i)}>
                      Resend
                    </Button>
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
          <div className="text-muted-foreground mt-1 text-[12px]">The old link stops working. This one works for 7 days.</div>
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
      <p className="text-muted-foreground mb-3 max-w-prose text-[13px] leading-relaxed">
        Anyone who signs in with a verified email at one of these domains joins {orgName} as a member, with no invite. Leave it empty to let people in by
        invite only.
      </p>
      <Label htmlFor="org-domains" className="mb-1 text-[13px]">
        Email domains, one per line
      </Label>
      <Textarea id="org-domains" className="mb-3 max-w-[360px] font-mono text-[13px]" rows={3} placeholder="example.com" value={text} onChange={(e) => setText(e.target.value)} />
      <Button size="sm" disabled={busy || !changed} onClick={() => void save()}>
        Save domains
      </Button>
    </Panel>
  );
}
