import { createFileRoute } from "@tanstack/react-router";
import { SectionHead } from "@/components/settings";
import { TokensPanel } from "@/components/tokens";
import { useAuth } from "@/lib/auth";

export const Route = createFileRoute("/w/$ws/settings/access")({ component: Access });

function Access() {
  const required = useAuth().data?.required;
  return (
    <>
      <SectionHead title="Let your agents and webhooks reach sopops">
        Make a token for each agent or service that sends sessions or fetches instructions.{" "}
        {required ? "People sign in instead: manage them under Members." : "People don't sign in on this sopops."}
      </SectionHead>
      <TokensPanel />
    </>
  );
}
