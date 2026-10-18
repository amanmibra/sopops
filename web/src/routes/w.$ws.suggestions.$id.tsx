import { createFileRoute } from "@tanstack/react-router";
import { SuggestionView } from "@/components/suggestions";

export const Route = createFileRoute("/w/$ws/suggestions/$id")({ component: Suggestion });

function Suggestion() {
  const { id } = Route.useParams();
  return <SuggestionView key={id} id={id} />;
}
