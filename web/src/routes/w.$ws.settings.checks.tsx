import { createFileRoute } from "@tanstack/react-router";
import { AIModelsForm } from "@/components/ai-models";
import { Panel } from "@/components/bits";
import { CalibrationCard } from "@/components/calibration";
import { PricesCard } from "@/components/insights/spend";
import { SectionHead } from "@/components/settings";

export const Route = createFileRoute("/w/$ws/settings/checks")({ component: Checks });

function Checks() {
  return (
    <>
      <SectionHead title="Choose the AI that checks your calls, and tune it">
        Your team's AI keys and models, fitting each procedure's checks to your reviews, and the model prices that spend in Insights is estimated with.
      </SectionHead>
      <Panel title="AI keys and models">
        <AIModelsForm />
      </Panel>
      <CalibrationCard />
      <PricesCard />
    </>
  );
}
