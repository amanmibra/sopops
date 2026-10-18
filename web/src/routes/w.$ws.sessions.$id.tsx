import { createFileRoute } from "@tanstack/react-router";
import { type SessionAt, SessionView } from "@/components/sessions";

const str = (v: unknown) => (typeof v === "string" && v ? v : typeof v === "number" ? String(v) : undefined);

// `procedure` + `rule` (and `turn`, `finding`) open the session at a finding, as links from Patterns do.
export const Route = createFileRoute("/w/$ws/sessions/$id")({
  validateSearch: (s: Record<string, unknown>): SessionAt => {
    const turn = Number(s.turn);
    return { procedure: str(s.procedure), rule: str(s.rule), turn: s.turn != null && Number.isInteger(turn) ? turn : undefined, finding: str(s.finding) };
  },
  component: Session,
});

function Session() {
  const { id } = Route.useParams();
  const at = Route.useSearch();
  return <SessionView key={id} id={id} at={at} />;
}
