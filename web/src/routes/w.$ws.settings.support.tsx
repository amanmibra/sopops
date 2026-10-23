import { createFileRoute } from "@tanstack/react-router";
import { SectionHead } from "@/components/settings";
import { SupportPanel } from "@/components/support";
import { useAuth } from "@/lib/auth";

export const Route = createFileRoute("/w/$ws/settings/support")({ component: SupportAccess });

function SupportAccess() {
  const auth = useAuth().data;
  if (!auth) return null;
  return (
    <>
      <SectionHead title="Support access">sopops support can open a session on your workspace. You see every one here.</SectionHead>
      <SupportPanel auth={auth} />
    </>
  );
}
