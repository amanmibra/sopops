import { createFileRoute } from "@tanstack/react-router";
import { SuggestionsView } from "@/components/suggestions";
import { SUGGESTION_STATUSES, type SuggestionStatus } from "@/lib/review";

export const Route = createFileRoute("/w/$ws/suggestions/")({
  validateSearch: (s: Record<string, unknown>): { status?: SuggestionStatus | "all" } => ({
    status: s.status === "all" ? "all" : SUGGESTION_STATUSES.find((v) => v === s.status),
  }),
  component: Suggestions,
});

function Suggestions() {
  const { status } = Route.useSearch();
  const navigate = Route.useNavigate();
  return <SuggestionsView status={status} onStatus={(s) => navigate({ search: { status: s }, replace: true })} />;
}
