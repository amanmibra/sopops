import { createFileRoute } from "@tanstack/react-router";
import { OverviewView } from "@/components/insights";
import { validateInsightSearch } from "@/lib/insights";

export const Route = createFileRoute("/w/$ws/insights/")({ validateSearch: validateInsightSearch, component: Overview });

function Overview() {
  const filters = Route.useSearch();
  const navigate = Route.useNavigate();
  return <OverviewView filters={filters} onFilters={(search) => navigate({ search, replace: true })} />;
}
