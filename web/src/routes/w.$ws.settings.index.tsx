import { createFileRoute } from "@tanstack/react-router";
import { SettingsEditor } from "@/components/editors";
import { ImportCard } from "@/components/onboarding/review";
import { RepoCard } from "@/components/repo";

export const Route = createFileRoute("/w/$ws/settings/")({ component: Settings });

function Settings() {
  return (
    <>
      <SettingsEditor />
      <div className="mt-8 max-w-[860px]">
        <RepoCard />
        <ImportCard />
      </div>
    </>
  );
}
