import { createFileRoute } from "@tanstack/react-router";
import { type PatternsSearch, PatternsView } from "@/components/review";
import { VERDICTS } from "@/lib/review";

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);
const TABS = ["spot", "open", "fixing", "fixed", "all"] as const;
const VERDICT_FILTERS = ["none", "has", "any", ...VERDICTS] as const;

export const Route = createFileRoute("/w/$ws/patterns/")({
  validateSearch: (s: Record<string, unknown>): PatternsSearch => ({
    tab: TABS.find((t) => t === s.tab),
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
