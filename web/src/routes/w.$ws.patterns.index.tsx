import { createFileRoute } from "@tanstack/react-router";
import { type PatternsSearch, PatternsView } from "@/components/review";
import { VERDICTS } from "@/lib/review";

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);
const VERDICT_FILTERS = ["none", "has", "any", ...VERDICTS] as const;

export const Route = createFileRoute("/w/$ws/patterns/")({
  validateSearch: (s: Record<string, unknown>): PatternsSearch => ({
    tab: s.tab === "all" ? "all" : undefined,
    procedure: str(s.procedure),
    theme: str(s.theme),
    verdict: VERDICT_FILTERS.find((v) => v === s.verdict),
  }),
  component: Patterns,
});

function Patterns() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <PatternsView search={search} onSearch={(s) => navigate({ search: s, replace: true })} />;
}
