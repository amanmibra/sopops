import { createFileRoute } from "@tanstack/react-router";
import { ProcedureEditor } from "@/components/editors";

export const Route = createFileRoute("/w/$ws/procedures/$id/")({ component: Editor });

function Editor() {
  const { id } = Route.useParams();
  return <ProcedureEditor key={id} id={id} />;
}
