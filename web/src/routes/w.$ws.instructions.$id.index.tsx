import { createFileRoute } from "@tanstack/react-router";
import { InstructionEditor } from "@/components/editors";

export const Route = createFileRoute("/w/$ws/instructions/$id/")({ component: Editor });

function Editor() {
  const { id } = Route.useParams();
  return <InstructionEditor key={id} id={id} />;
}
