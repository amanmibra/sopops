import { createFileRoute } from "@tanstack/react-router";
import { CalibrationCard } from "@/components/calibration";
import { SettingsEditor } from "@/components/editors";
import { ImportCard } from "@/components/onboarding/review";
import { PricesCard } from "@/components/insights/spend";
import { RepoCard } from "@/components/repo";

export const Route = createFileRoute("/w/$ws/settings/")({ component: Settings });

function Settings() {
  return (
    <>
      <SettingsEditor />
      <div className="mt-8 max-w-[860px]">
        <RepoCard />
        <CalibrationCard />
        <PricesCard />
        <ImportCard />
      </div>
    </>
  );
}
