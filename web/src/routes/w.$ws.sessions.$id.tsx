import { createFileRoute } from "@tanstack/react-router";
import { SessionView } from "@/components/sessions";

export const Route = createFileRoute("/w/$ws/sessions/$id")({ component: Session });

function Session() {
  const { id } = Route.useParams();
  return <SessionView key={id} id={id} />;
}
