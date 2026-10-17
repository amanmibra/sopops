import { createFileRoute } from "@tanstack/react-router";
import { HistoryView } from "@/components/history-view";

export const Route = createFileRoute("/w/$ws/settings/history")({ component: SettingsHistory });

function SettingsHistory() {
  return <HistoryView kind="settings" id="settings" />;
}
