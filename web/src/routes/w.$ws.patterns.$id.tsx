import { createFileRoute } from "@tanstack/react-router";
import { PatternView } from "@/components/review";

export const Route = createFileRoute("/w/$ws/patterns/$id")({ component: Pattern });

function Pattern() {
  const { id } = Route.useParams();
  return <PatternView key={id} id={id} />;
}
