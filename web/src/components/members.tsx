// Settings → Members: the org's people and roles, invites by link (no email is sent), and the email domains whose people
// join by signing in. Owners and admins change things; members see who's here.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Globe, MailPlus, Users } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { type AuthState, type InviteOut, type MemberOut, call, sdk } from "@/lib/api";
import { when } from "@/lib/kinds";
import { Avatar } from "./account";
import { Banner, ConfirmButton, ErrorBanner, Note, Panel, Tag } from "./bits";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Textarea } from "./ui/textarea";

type Role = MemberOut["role"];

const ROLES: { role: Role; label: string; help: string }[] = [
  { role: "member", label: "Member", help: "Uses everything in sopops." },
  { role: "admin", label: "Admin", help: "Also invites people, changes roles and sets the org's email domains." },
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
      <People auth={auth} manager={manager} />
      {manager && <Invites isOwner={role === "owner"} />}
      {manager && org.data && <Domains domains={org.data.allowed_domains} orgName={org.data.name} />}
      {!manager && <Note>Owners and admins invite people and change roles.</Note>}
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

function Invites({ isOwner }: { isOwner: boolean }) {
  const qc = useQueryClient();
  const list = useQuery(mq.invites());
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [made, setMade] = useState<{ email: string; link: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["org", "invites"] });
  const invite = async () => {
    setBusy(true);
    try {
      const got = await call(sdk.createInvite({ body: { email: email.trim(), role } }));
      setMade({ email: got.email, link: got.link });
      setEmail("");
      await refresh();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  const withdraw = async (i: InviteOut) => {
    try {
      await call(sdk.revokeInvite({ path: { id: i.id } }));
      await refresh();
      toast.success("Invite withdrawn. Its link stops working now.");
    } catch (e) {
      fail(e);
    }
  };
  const copy = (text: string) => navigator.clipboard.writeText(text).then(() => toast.success("Copied."));
  const invites = list.data ?? [];
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <MailPlus className="size-4" /> Invite someone
        </span>
      }
    >
      <p className="text-muted-foreground mb-3 max-w-prose text-[13px] leading-relaxed">
        sopops doesn't send email. Make a link here and send it to them yourself. It works once, for 7 days, for whoever signs in with that email.
      </p>
      {made && (
        <Banner tone="ok">
          <b>Send this link to {made.email}. It isn't shown again.</b>
          <div className="bg-card mt-2 flex items-center gap-2 rounded-md border px-2.5 py-1.5">
            <code className="flex-1 font-mono text-[13px] break-all">{made.link}</code>
            <Button size="icon-sm" variant="ghost" aria-label="Copy invite link" onClick={() => void copy(made.link)}>
              <Copy />
            </Button>
          </div>
        </Banner>
      )}
      <form
        className="mt-3 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void invite();
        }}
      >
        <div className="grid gap-1">
          <Label htmlFor="invite-email" className="text-[13px]">
            Email
          </Label>
          <Input id="invite-email" type="email" required className="h-8 w-[260px]" placeholder="name@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="grid gap-1">
          <Label className="text-[13px]">Role</Label>
          <Select value={role} onValueChange={(v) => setRole(v as Role)}>
            <SelectTrigger size="sm" className="w-28" aria-label="Role">
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
        </div>
        <Button type="submit" size="sm" disabled={busy || !email.trim()}>
          Make invite link
        </Button>
      </form>
      {list.isError && <ErrorBanner error={list.error} lead="The invites didn't load:" />}
      {invites.length > 0 && (
        <>
          <h3 className="mt-5 mb-1 text-[13px] font-semibold">Waiting to be used</h3>
          <table className="w-full text-[13px]">
            <tbody>
              {invites.map((i) => (
                <tr key={i.id}>
                  <td className="border-b py-1.5 pr-2">{i.email}</td>
                  <td className="border-b py-1.5 pr-2">
                    <Tag>{i.role}</Tag>
                  </td>
                  <td className="text-muted-foreground border-b py-1.5 pr-2">expires {when(i.expires_at)}</td>
                  <td className="border-b py-1.5 text-right">
                    <ConfirmButton size="sm" variant="ghost" className="text-err" confirm="Click again to withdraw" onConfirm={() => void withdraw(i)}>
                      Withdraw
                    </ConfirmButton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
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
