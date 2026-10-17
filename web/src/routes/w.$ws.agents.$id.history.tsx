import { createFileRoute } from "@tanstack/react-router";
import { HistoryView } from "@/components/history-view";

export const Route = createFileRoute("/w/$ws/agents/$id/history")({ component: History });

function History() {
  const { id } = Route.useParams();
  return <HistoryView key={id} kind="agent" id={id} />;
}
