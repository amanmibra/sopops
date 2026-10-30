import { createFileRoute } from "@tanstack/react-router";
import { SessionsView } from "@/components/sessions";
import type { SessionFilters } from "@/lib/api";
import { tagList } from "@/lib/insights";

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);
const oneOf = <T extends string>(v: unknown, options: readonly T[]) => (options.includes(v as T) ? (v as T) : undefined);
const RESULTS = ["deviated", "partial", "followed", "not_measurable"] as const;
const RULE_STATUSES = ["done", "out_of_order", "skipped", "not_reached", "not_applicable", "violated", "ok", "present", "unheeded", "absent"] as const;

export const Route = createFileRoute("/w/$ws/sessions/")({
  validateSearch: (s: Record<string, unknown>): SessionFilters => {
    const statuses = (Array.isArray(s.rule_status) ? s.rule_status : s.rule_status != null ? [s.rule_status] : [])
      .map((v) => oneOf(v, RULE_STATUSES))
      .filter((v): v is (typeof RULE_STATUSES)[number] => !!v);
    const release = Number(s.release);
    return {
      agent: str(s.agent),
      channel: oneOf(s.channel, ["voice", "text"] as const),
      status: oneOf(s.status, ["open", "received", "routed", "checked", "excluded", "incomplete", "failed"] as const),
      result: oneOf(s.result, RESULTS),
      since: str(s.since),
      until: str(s.until),
      release: s.release != null && Number.isInteger(release) ? release : undefined,
      procedure: str(s.procedure),
      procedure_result: oneOf(s.procedure_result, RESULTS),
      rule: str(s.rule) ?? (typeof s.rule === "number" ? String(s.rule) : undefined),
      rule_status: statuses.length ? statuses : undefined,
      pattern: str(s.pattern),
      theme: str(s.theme),
      finding: typeof s.finding === "boolean" ? s.finding : undefined,
      eligible: typeof s.eligible === "boolean" ? s.eligible : undefined,
      suppressed: typeof s.suppressed === "boolean" ? s.suppressed : undefined,
      tag: tagList(s.tag),
    };
  },
  component: Sessions,
});

function Sessions() {
  const filters = Route.useSearch();
  const navigate = Route.useNavigate();
  return <SessionsView filters={filters} onFilters={(search) => navigate({ search, replace: true })} />;
}
