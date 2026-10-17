import { createFileRoute } from "@tanstack/react-router";
import { RuleInsightsView } from "@/components/insights";
import { validateInsightSearch } from "@/lib/insights";

export const Route = createFileRoute("/w/$ws/insights/procedures/$id/rules/$ruleId")({ validateSearch: validateInsightSearch, component: RuleInsights });

function RuleInsights() {
  const { id, ruleId } = Route.useParams();
  const filters = Route.useSearch();
  const navigate = Route.useNavigate();
  return <RuleInsightsView key={`${id}/${ruleId}`} procedureId={id} ruleId={ruleId} filters={filters} onFilters={(search) => navigate({ search, replace: true })} />;
}
