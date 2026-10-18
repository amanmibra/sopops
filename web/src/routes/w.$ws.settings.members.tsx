import { createFileRoute } from "@tanstack/react-router";
import { MembersPanel } from "@/components/members";
import { SectionHead } from "@/components/settings";
import { useAuth } from "@/lib/auth";

export const Route = createFileRoute("/w/$ws/settings/members")({ component: Members });

function Members() {
  const auth = useAuth().data;
  if (!auth) return null;
  if (!auth.required)
    return (
      <SectionHead title="Choose who can use sopops">
        People don't sign in on this sopops, so there are no members to manage. To have people sign in with GitHub or Google, set up a login provider (see
        "Sign in" in the README) and restart sopops.
      </SectionHead>
    );
  return (
    <>
      <SectionHead title="Choose who can use sopops">
        Everyone here can use every workspace in {auth.org?.name}. Owners and admins invite people, change roles and let email domains join.
      </SectionHead>
      <MembersPanel auth={auth} />
    </>
  );
}
