import { createFileRoute } from "@tanstack/react-router";
import { RuntimePanels } from "@/components/runtime";
import { SectionHead } from "@/components/settings";
import { PlatformsYouUse } from "@/components/setup";

export const Route = createFileRoute("/w/$ws/settings/platforms")({ component: Platforms });

function Platforms() {
  return (
    <>
      <SectionHead title="Connect your agents to sopops">
        Each agent gets its instructions from sopops and sends its calls back to be checked. The two are set up separately, and calls can come back with no code
        even while the instructions stay in your agent's code. Start with the table, then add keys for the platforms you use.
      </SectionHead>
      <PlatformsYouUse />
      <RuntimePanels />
    </>
  );
}
