import { createFileRoute } from "@tanstack/react-router";
import { ReleaseView } from "@/components/views";

export const Route = createFileRoute("/w/$ws/releases/$number")({ component: Release });

function Release() {
  const { number } = Route.useParams();
  return <ReleaseView key={number} number={Number(number)} />;
}
