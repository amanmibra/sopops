// Helpers over a workspace's config: what a block id is, what people call it, what an agent's blocks expand to.
import type { AgentConfig, ConfigResponse, GroupConfig, InstructionConfig, ProcedureConfig, SettingsConfig, StepOutput } from "./api";
import { BLOCK_KINDS, type BlockKind, KIND, type Kind, deliveryName, platformName } from "./kinds";

export type Step = { text: string; tool?: string | null; required?: boolean };
export type Item = AgentConfig | InstructionConfig | ProcedureConfig | GroupConfig;

const LISTS = { agent: "agents", instruction: "instructions", procedure: "procedures", group: "groups" } as const;

export class Model {
  cfg: ConfigResponse;
  constructor(cfg: ConfigResponse) {
    this.cfg = cfg;
  }

  list(kind: Exclude<Kind, "settings">): Item[] {
    return this.cfg[LISTS[kind]];
  }

  find(kind: Kind, id: string): Item | SettingsConfig | undefined {
    if (kind === "settings") return this.cfg.settings;
    return this.list(kind).find((x) => x.id === id);
  }

  /** "instruction", "procedure", "group", or null if nothing has that name. */
  blockKind(id: string): BlockKind | null {
    for (const k of BLOCK_KINDS) if (this.list(k).some((x) => x.id === id)) return k;
    return null;
  }

  /** A procedure's name, else the id. */
  itemName(kind: Kind | "sop" | "config" | "base", id: string): string {
    if (kind === "settings" || kind === "config") return "Settings";
    if (kind === "procedure" || kind === "sop") return this.cfg.procedures.find((p) => p.id === id)?.name ?? id;
    return id;
  }

  blockName = (id: string) => this.itemName(this.blockKind(id) ?? "instruction", id);

  /** An agent's blocks with groups expanded: [{id, via}] (via: the group the agent lists, or null). */
  expand(blocks: string[], via: string | null = null, seen = new Set<string>()): { id: string; via: string | null }[] {
    const out: { id: string; via: string | null }[] = [];
    for (const b of blocks) {
      const g = this.cfg.groups.find((x) => x.id === b);
      if (g) {
        if (!seen.has(b)) out.push(...this.expand(g.blocks, via ?? b, new Set([...seen, b])));
      } else out.push({ id: b, via });
    }
    return out;
  }

  blockText(id: string): string {
    const k = this.blockKind(id);
    if (k === "instruction") return (this.find(k, id) as InstructionConfig).text;
    if (k === "procedure") {
      const p = this.find(k, id) as ProcedureConfig;
      return [p.name, p.goal, p.when, p.guidance, ...[...p.steps, ...p.never, ...p.warning_signs].map((s) => s.text)].join("\n");
    }
    return "";
  }

  /** Variables an agent's context and blocks use, with the blocks that use each, in first-use order. */
  neededVariables(agent: { context: string; blocks: string[] }): Map<string, string[]> {
    const need = new Map<string, string[]>();
    const add = (name: string, from: string) => {
      const list = need.get(name) ?? [];
      if (!list.includes(from)) list.push(from);
      need.set(name, list);
    };
    placeholders(agent.context).forEach((n) => add(n, "About this agent"));
    for (const { id } of this.expand(agent.blocks)) placeholders(this.blockText(id)).forEach((n) => add(n, this.blockName(id)));
    return need;
  }

  /** An item as plain lines, for history and change views. Never YAML: the fields, in words. */
  readable(kind: Kind, it: Record<string, unknown> | null | undefined): string[] {
    if (!it || it.deleted) return ["(deleted)"];
    if (it.legacy) return ["(saved in the format of sopc v0.0.8 and earlier)", ...String(it.content ?? "").replace(/\n+$/, "").split("\n")];
    const list = (title: string, items: Step[], numbered = false) =>
      items.length ? [title, ...items.map((s, i) => `  ${numbered ? `${i + 1}.` : "-"} ${stepLine(s)}`)] : [];
    const blocks = (ids: string[]) =>
      ids.length
        ? ["Blocks:", ...ids.map((b, i) => `  ${i + 1}. ${this.blockName(b)}${this.blockKind(b) ? ` (${KIND[this.blockKind(b)!].badge})` : ""}`)]
        : ["Blocks: none"];
    const vars = (title: string, v: Record<string, string>) =>
      Object.keys(v).length ? [title, ...Object.entries(v).map(([k, x]) => `  ${k} = ${x}`)] : [`${title} none`];
    const block = (title: string, t: string) => (t ? [title, ...t.replace(/\n+$/, "").split("\n").map((l) => "  " + l)] : [`${title} (empty)`]);
    const a = it as Record<string, never>;
    if (kind === "agent")
      return [`Platform: ${platformName(a.platform)}, ${a.platform_id}`, ...block("About this agent:", a.context), ...blocks(a.blocks), ...vars("Variables:", a.variables)];
    if (kind === "instruction") return block("Text:", a.text);
    if (kind === "procedure")
      return [
        `Name: ${a.name}`,
        `Goal: ${a.goal}`,
        `When: ${a.when}`,
        ...block("Guidance:", a.guidance),
        ...list("Steps:", a.steps, true),
        ...list("Never:", a.never),
        ...list("Warning signs:", a.warning_signs),
        `Delivery: ${deliveryName(a.delivery)}`,
      ];
    if (kind === "group") return blocks(a.blocks);
    return [...vars("Default variables:", a.variables), `Procedures heading: ${a.procedures_heading || "(none)"}`];
  }
}

export const placeholders = (t: string | null | undefined) => [...String(t ?? "").matchAll(/\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}/g)].map((m) => m[1]);
export const stepLine = (s: Step | StepOutput) => `${s.text}${s.tool ? `  [tool: ${s.tool}${s.required ? ", required" : ""}]` : ""}`;
export const cleanStep = (s: Step) => ({ text: s.text, tool: s.tool && s.tool.trim() ? s.tool.trim() : null, required: !!s.required });
