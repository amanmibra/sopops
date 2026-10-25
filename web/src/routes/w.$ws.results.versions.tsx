import { createFileRoute } from "@tanstack/react-router";
import { VersionsView } from "@/components/insights";
import type { InsightFilters } from "@/lib/api";
import { validateInsightSearch } from "@/lib/insights";

type Search = InsightFilters & { test?: string; v?: string[] };
const strs = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : typeof v === "string" && v ? [v] : undefined);

export const Route = createFileRoute("/w/$ws/results/versions")({
  validateSearch: (s: Record<string, unknown>): Search => ({ ...validateInsightSearch(s), test: typeof s.test === "string" && s.test ? s.test : undefined, v: strs(s.v) }),
  component: Versions,
});

function Versions() {
  const { test, v, ...filters } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <VersionsView
      filters={filters}
      test={test}
      v={v}
      onFilters={(f) => navigate({ search: { ...f, test, v }, replace: true })}
      onTest={(t) => navigate({ search: { ...filters, test: t, v: undefined }, replace: true })}
      onPick={(nv) => navigate({ search: { ...filters, test, v: nv.length ? nv : undefined }, replace: true })}
    />
  );
}
