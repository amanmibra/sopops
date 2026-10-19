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
        People don't sign in on this sopops, so there are no members to manage. To have people sign in, set SOPOPS_AUTH_REQUIRED=true for email and
        password, or set up GitHub, Google or SMTP (see "Sign in" in the README), then restart sopops.
      </SectionHead>
    );
  return (
    <>
      <SectionHead title="Choose who can use sopops">
        Everyone in {auth.org?.name} can use everything in it. Owners and admins bring people in, change roles and let email domains join.
      </SectionHead>
      <MembersPanel auth={auth} />
    </>
  );
}
