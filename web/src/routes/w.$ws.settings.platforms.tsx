import { createFileRoute } from "@tanstack/react-router";
import { ImportCard } from "@/components/onboarding/review";
import { RuntimePanels } from "@/components/runtime";
import { SectionHead } from "@/components/settings";
import { PlatformsYouUse } from "@/components/setup";

export const Route = createFileRoute("/w/$ws/settings/platforms")({ component: Platforms });

function Platforms() {
  return (
    <>
      <SectionHead title="Connect your agents to sopops">
        Each agent gets its prompt from sopops and sends its calls back to be checked. Start with the table, then add keys for the platforms you use.
      </SectionHead>
      <PlatformsYouUse />
      <RuntimePanels />
      <ImportCard />
    </>
  );
}
