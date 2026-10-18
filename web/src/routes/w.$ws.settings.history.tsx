import { createFileRoute } from "@tanstack/react-router";
import { HistoryView } from "@/components/history-view";
import { SectionHead } from "@/components/settings";

export const Route = createFileRoute("/w/$ws/settings/history")({ component: SettingsHistory });

function SettingsHistory() {
  return (
    <HistoryView
      kind="settings"
      id="settings"
      head={
        <SectionHead title="Earlier versions of the instruction defaults">
          Pick a version to see what changed since. You can restore it into the form.
        </SectionHead>
      }
    />
  );
}
