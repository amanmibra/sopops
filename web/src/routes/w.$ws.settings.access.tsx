import { createFileRoute } from "@tanstack/react-router";
import { SectionHead } from "@/components/settings";
import { TokensPanel } from "@/components/tokens";

export const Route = createFileRoute("/w/$ws/settings/access")({ component: Access });

function Access() {
  return (
    <>
      <SectionHead title="Let your agents and webhooks reach sopops">
        Make a token for each agent or service that sends sessions or fetches prompts. The rest of sopops has no sign-in yet.
      </SectionHead>
      <TokensPanel />
    </>
  );
}
