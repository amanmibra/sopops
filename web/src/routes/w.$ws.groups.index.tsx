import { createFileRoute } from "@tanstack/react-router";
import { ListView } from "@/components/views";

export const Route = createFileRoute("/w/$ws/groups/")({ component: List });

function List() {
  return <ListView kind="group" />;
}
