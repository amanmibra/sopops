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

// The platforms sopops supports, LiveKit first. sop also knows `retell`; an agent that already uses it keeps it
// (`legacy`), but it isn't offered for new agents.
export const PLATFORMS = [
  {
    value: "livekit",
    label: "LiveKit",
    idLabel: "Identifier",
    idHelp: "The unique value your code passes as agent when fetching instructions.",
    legacy: false,
  },
  { value: "elevenlabs", label: "ElevenLabs", idLabel: "Agent ID", idHelp: "The agent's id on ElevenLabs. It has to match, so published instructions and post-call webhooks find it.", legacy: false },
  { value: "vapi", label: "Vapi", idLabel: "Assistant ID", idHelp: "The assistant's id on Vapi. It has to match, so published instructions and end-of-call reports find it.", legacy: false },
  { value: "retell", label: "Retell", idLabel: "Agent ID", idHelp: "The agent's id on Retell.", legacy: true },
] as const;
export const platformName = (p: string) => PLATFORMS.find((x) => x.value === p)?.label ?? p;
/** A stored `platform_ref` (`livekit:tonys-pizza`) shown like the Agents list: "LiveKit: tonys-pizza". Empty for none. */
export function platformRefName(ref?: string | null): string {
  if (!ref) return "";
  const i = ref.indexOf(":");
  return i < 0 ? ref : `${platformName(ref.slice(0, i))}: ${ref.slice(i + 1)}`;
}

export const DELIVERY = [
  { value: "prompt", label: "Whole procedure in the instructions", help: "Everything goes in the agent's instructions. Use this unless the agent has a get_sop tool." },
  {
    value: "auto",
    label: "Rules in the instructions, steps on request",
    help: "The name, when it applies, never and warning signs stay in the instructions. The goal, guidance and steps are fetched with the agent's get_sop tool.",
  },
  { value: "tool", label: "Only the name in the instructions, the rest on request", help: "The instructions name the procedure and when it applies. Everything else is fetched with the agent's get_sop tool." },
] as const;
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

/** The config file's names: sop.yaml, or sopc.yaml in a project made before sop v0.1.0 (sop.yaml wins if both are there). */
export const CONFIG_FILES = ["sop.yaml", "sopc.yaml"];

export function itemOfPath(path: string): [Kind, string] | null {
  if (CONFIG_FILES.includes(path)) return ["settings", "settings"];
  const [dir, name] = path.split("/");
  const kind = ({ agents: "agent", instructions: "instruction", bases: "instruction", procedures: "procedure" } as Record<string, Kind>)[dir];
  return kind ? [kind, (name || "").replace(/\.(md|yaml)$/, "")] : null;
}
