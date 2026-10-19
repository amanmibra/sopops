import { Navigate, createFileRoute } from "@tanstack/react-router";
import { TeamSetup } from "@/components/setup";
import { FLOWS, type Flow, type StepKey } from "@/lib/setup";

type Search = { step: StepKey; flow: Flow };

const TEAM_STEPS: StepKey[] = ["people", "platforms", "blocks", "agents", "publish", "connect"];

export const Route = createFileRoute("/w/$ws/setup")({
  validateSearch: (s: Record<string, unknown>): Search => {
    const flow = (typeof s.flow === "string" && s.flow in FLOWS ? s.flow : "solo") as Flow;
    const step = typeof s.step === "string" && FLOWS[flow].includes(s.step as StepKey) ? (s.step as StepKey) : FLOWS[flow].find((x) => TEAM_STEPS.includes(x))!;
    return { step, flow };
  },
  component: Setup,
});

/** The setup steps inside a team: people, platforms, how a prompt is built, agents, publishing and calls. */
function Setup() {
  const { ws } = Route.useParams();
  const { step, flow } = Route.useSearch();
  if (!TEAM_STEPS.includes(step)) return <Navigate to="/welcome" replace />;
  return <TeamSetup key={`${ws}:${step}`} flow={flow} step={step} />;
}
