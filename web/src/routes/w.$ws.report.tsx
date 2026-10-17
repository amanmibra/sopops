import { createFileRoute } from "@tanstack/react-router";
import { ReportView } from "@/components/report";
import { validateInsightSearch } from "@/lib/insights";

export const Route = createFileRoute("/w/$ws/report")({ validateSearch: validateInsightSearch, component: Report });

function Report() {
  const filters = Route.useSearch();
  const navigate = Route.useNavigate();
  return <ReportView filters={filters} onFilters={(search) => navigate({ search, replace: true })} />;
}
