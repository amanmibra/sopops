import { createFileRoute } from "@tanstack/react-router";
import { PromptView } from "@/components/views";

export const Route = createFileRoute("/w/$ws/agents/$id/prompt")({ component: Prompt });

function Prompt() {
  const { id } = Route.useParams();
  return <PromptView key={id} id={id} />;
}
