// What each kind of item is called, and the fixed choices the forms offer.

export type Kind = "agent" | "instruction" | "procedure" | "group" | "settings";
export type BlockKind = "instruction" | "procedure" | "group";
export type ListKind = Exclude<Kind, "settings">;

export const KIND: Record<Kind, { one: string; many: string; route: string; badge?: string }> = {
  agent: { one: "Agent", many: "Agents", route: "agents" },
  instruction: { one: "Shared instruction", many: "Shared instructions", route: "instructions", badge: "instruction" },
  procedure: { one: "Procedure", many: "Procedures", route: "procedures", badge: "procedure" },
  group: { one: "Group", many: "Groups", route: "groups", badge: "group" },
  settings: { one: "Settings", many: "Settings", route: "settings" },
};

export const BLOCK_KINDS: BlockKind[] = ["group", "instruction", "procedure"];

export const PLATFORMS = [
  { value: "livekit", label: "LiveKit", idLabel: "Agent name" },
  { value: "vapi", label: "Vapi", idLabel: "Assistant ID" },
  { value: "elevenlabs", label: "ElevenLabs", idLabel: "Agent ID" },
  { value: "retell", label: "Retell", idLabel: "Agent ID" },
] as const;
export type Platform = (typeof PLATFORMS)[number]["value"];
export const platformName = (p: string) => PLATFORMS.find((x) => x.value === p)?.label ?? p;

export const DELIVERY = [
  { value: "prompt", label: "Whole procedure in the prompt", help: "Everything goes in the agent's prompt. Use this unless the agent has a get_sop tool." },
  {
    value: "auto",
    label: "Rules in the prompt, steps on request",
    help: "The name, when it applies, never and warning signs stay in the prompt. The goal, guidance and steps are fetched with the agent's get_sop tool.",
  },
  { value: "tool", label: "Only the name in the prompt, the rest on request", help: "The prompt names the procedure and when it applies. Everything else is fetched with the agent's get_sop tool." },
] as const;
export type Delivery = (typeof DELIVERY)[number]["value"];
export const deliveryName = (d: string) => DELIVERY.find((x) => x.value === d)?.label ?? d;

export const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
export const slug = (s: string) =>
  s
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);
export const when = (t?: string | null) => (t ? new Date(t).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "");
export const ITEM_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/;
export const WORKSPACE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

// The kind of item a component in a release or draft refers to.
export const kindOfComponent = (k: string): Kind =>
  (({ agent: "agent", instruction: "instruction", base: "instruction", sop: "procedure", config: "settings" }) as Record<string, Kind>)[k] ?? "instruction";

export function itemOfPath(path: string): [Kind, string] | null {
  if (path === "sopc.yaml") return ["settings", "settings"];
  const [dir, name] = path.split("/");
  const kind = ({ agents: "agent", instructions: "instruction", bases: "instruction", procedures: "procedure" } as Record<string, Kind>)[dir];
  return kind ? [kind, (name || "").replace(/\.(md|yaml)$/, "")] : null;
}
