import { createFileRoute } from "@tanstack/react-router";
import { CalibrationCard } from "@/components/calibration";
import { PricesCard } from "@/components/insights/spend";
import { SectionHead } from "@/components/settings";

export const Route = createFileRoute("/w/$ws/settings/checks")({ component: Checks });

function Checks() {
  return (
    <>
      <SectionHead title="Tune the checks and see what they cost">
        Fit each procedure's checks to your reviews, and see the model prices that spend in Insights is estimated with.
      </SectionHead>
      <CalibrationCard />
      <PricesCard />
    </>
  );
}
