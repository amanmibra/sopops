import { createFileRoute } from "@tanstack/react-router";
import { GroupEditor } from "@/components/editors";

export const Route = createFileRoute("/w/$ws/groups/$id/")({ component: Editor });

function Editor() {
  const { id } = Route.useParams();
  return <GroupEditor key={id} id={id} />;
}
