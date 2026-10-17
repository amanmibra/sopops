import { createFileRoute } from "@tanstack/react-router";
import { ProcedureInsightsView } from "@/components/insights";
import { validateInsightSearch } from "@/lib/insights";

export const Route = createFileRoute("/w/$ws/insights/procedures/$id/")({ validateSearch: validateInsightSearch, component: ProcedureInsights });

function ProcedureInsights() {
  const { id } = Route.useParams();
  const filters = Route.useSearch();
  const navigate = Route.useNavigate();
  return <ProcedureInsightsView key={id} id={id} filters={filters} onFilters={(search) => navigate({ search, replace: true })} />;
}
