import { createFileRoute } from "@tanstack/react-router";
import { ImportView } from "@/components/onboarding/review";

export const Route = createFileRoute("/w/$ws/import/$id")({ component: Page });

function Page() {
  const { id } = Route.useParams();
  return <ImportView key={id} id={id} />;
}
