import { createFileRoute } from "@tanstack/react-router";
import { CompareView } from "@/components/insights";
import type { InsightFilters } from "@/lib/api";
import { validateInsightSearch } from "@/lib/insights";

type Search = InsightFilters & { a?: number; b?: number };
const int = (v: unknown) => (v != null && v !== "" && Number.isInteger(Number(v)) ? Number(v) : undefined);

export const Route = createFileRoute("/w/$ws/insights/releases")({
  validateSearch: (s: Record<string, unknown>): Search => ({ ...validateInsightSearch(s), a: int(s.a), b: int(s.b) }),
  component: Compare,
});

function Compare() {
  const { a, b, ...filters } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <CompareView
      filters={filters}
      a={a}
      b={b}
      onFilters={(f) => navigate({ search: { ...f, a, b }, replace: true })}
      onPick={(na, nb) => navigate({ search: { ...filters, a: na, b: nb }, replace: true })}
    />
  );
}
