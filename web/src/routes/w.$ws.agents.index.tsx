import { createFileRoute } from "@tanstack/react-router";
import { ListView } from "@/components/views";

export const Route = createFileRoute("/w/$ws/agents/")({ component: List });

function List() {
  return <ListView kind="agent" />;
}
