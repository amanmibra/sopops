// Setting up: the numbered steps, in the order each kind of person sees them, and where this browser is in them.
// The first person on a fresh install sees all nine; with sign-in off there's no account or team to make, so it starts
// at platforms; someone joining a team makes an account, and picks the platforms only if nobody has yet.
import { prefs } from "./prefs";

export type StepKey = "name" | "account" | "team" | "people" | "platforms" | "blocks" | "agents" | "publish" | "connect";
export type Flow = "first" | "solo" | "join";

export const FLOWS: Record<Flow, StepKey[]> = {
  first: ["name", "account", "team", "people", "platforms", "blocks", "agents", "publish", "connect"],
  solo: ["platforms", "blocks", "agents", "publish", "connect"],
  join: ["name", "account", "platforms"],
};

/** Each step's question, as its screen's title. */
export const STEP_TITLE: Record<StepKey, string> = {
  name: "What's your name?",
  account: "Pick an email and password",
  team: "What's your team called?",
  people: "Want anyone else on your team?",
  platforms: "Which platforms do you use?",
  blocks: "How sopops builds an agent's instructions",
  agents: "Bring in your agents",
  publish: "Publish your first release",
  connect: "Connect your calls",
};

export const stepNumber = (flow: Flow, step: StepKey) => FLOWS[flow].indexOf(step) + 1;
export const nextStep = (flow: Flow, step: StepKey): StepKey | null => FLOWS[flow][FLOWS[flow].indexOf(step) + 1] ?? null;
export const prevStep = (flow: Flow, step: StepKey): StepKey | null => FLOWS[flow][FLOWS[flow].indexOf(step) - 1] ?? null;

export const setupHref = (ws: string, step: StepKey, flow: Flow) => `/w/${encodeURIComponent(ws)}/setup?step=${step}&flow=${flow}`;

/** The step this browser left setup at, for the bar that leads back to it. */
export const setupProgress = {
  get(ws: string): { flow: Flow; step: StepKey } | null {
    const [flow, step] = (prefs.get(`setup.${ws}`) ?? "").split(":");
    return flow in FLOWS && FLOWS[flow as Flow].includes(step as StepKey) ? { flow: flow as Flow, step: step as StepKey } : null;
  },
  set(ws: string, flow: Flow, step: StepKey) {
    prefs.set(`setup.${ws}`, `${flow}:${step}`);
    prefs.set(`setup-started.${ws}`, "1");
  },
  clear(ws: string) {
    prefs.remove(`setup.${ws}`);
  },
  started: (ws: string) => prefs.get(`setup-started.${ws}`) === "1",
};

/** The "Get set up" card on a new team's home, once hidden, stays hidden in this browser. */
export const checklist = {
  hidden: (ws: string) => prefs.get(`checklist.${ws}`) === "hidden",
  hide: (ws: string) => prefs.set(`checklist.${ws}`, "hidden"),
};
