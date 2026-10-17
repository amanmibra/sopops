import { createFileRoute } from "@tanstack/react-router";
import { AgentEditor } from "@/components/editors";

export const Route = createFileRoute("/w/$ws/agents/$id/")({ component: Editor });

function Editor() {
  const { id } = Route.useParams();
  return <AgentEditor key={id} id={id} />;
}
