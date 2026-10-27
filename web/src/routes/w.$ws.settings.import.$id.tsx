import { createFileRoute } from "@tanstack/react-router";
import { ImportView } from "@/components/onboarding/review";

/** One import, inside settings beside the Import page, so the rail stays on Settings. */
export const Route = createFileRoute("/w/$ws/settings/import/$id")({ component: Page });

function Page() {
  const { id } = Route.useParams();
  return <ImportView key={id} id={id} />;
}
