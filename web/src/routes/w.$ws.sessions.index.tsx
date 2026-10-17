import { createFileRoute } from "@tanstack/react-router";
import { SessionsView } from "@/components/sessions";
import type { SessionFilters } from "@/lib/api";

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);
const oneOf = <T extends string>(v: unknown, options: readonly T[]) => (options.includes(v as T) ? (v as T) : undefined);

export const Route = createFileRoute("/w/$ws/sessions/")({
  validateSearch: (s: Record<string, unknown>): SessionFilters => ({
    agent: str(s.agent),
    channel: oneOf(s.channel, ["voice", "text"] as const),
    status: oneOf(s.status, ["received", "routed", "checked", "excluded", "partial", "failed"] as const),
    since: str(s.since),
    until: str(s.until),
  }),
  component: Sessions,
});

function Sessions() {
  const filters = Route.useSearch();
  const navigate = Route.useNavigate();
  return <SessionsView filters={filters} onFilters={(search) => navigate({ search, replace: true })} />;
}
