/**
 * LiveKit Agents (Node) adapter: post each finished session to sopops when the job shuts down.
 *
 *   const ins = await sopops.fetchInstructions(`livekit:${ctx.job.agentName}`);
 *   const session = new voice.AgentSession({ ... });
 *   recordSession(ctx, session, { sopops, agent: ins.agent, release: ins.release });
 *
 * This package doesn't depend on `@livekit/agents`; the shapes below are the parts of `JobContext` and
 * `voice.AgentSession` (v1) it reads.
 */
import type { AgentConfig, SessionIn, Sopops } from "./index.js";

type Item = Record<string, any>;
type Turn = NonNullable<SessionIn["turns"]>[number];
type ToolCall = NonNullable<SessionIn["tool_calls"]>[number];

/** The parts of `JobContext` used here. */
export interface LiveKitJobContext {
  job: { id: string; agentName?: string };
  room: { name?: string };
  addShutdownCallback(callback: () => Promise<void>): void;
}

/** A model as LiveKit plugins describe it. */
interface LiveKitModel {
  model?: string;
  provider?: string;
  voice?: unknown;
}

/** The parts of `voice.AgentSession` used here. */
export interface LiveKitSession {
  history: { toJSON(options: { excludeTimestamp: boolean }): unknown };
  llm?: LiveKitModel | null;
  stt?: LiveKitModel | null;
  tts?: LiveKitModel | null;
}

export interface RecordSessionOptions {
  sopops: Sopops;
  /** The sopc agent id. Default: the job's agent name. */
  agent?: string | null;
  /** The release the call ran on (`Instructions.release`). */
  release?: number | null;
  /** An A/B test this call is part of. */
  test?: string | null;
  /** Settings to report beyond the models read from the session (`agentConfig`); these win. */
  config?: AgentConfig;
}

/**
 * What the session runs with: `llm`, `stt`, `tts` as `<provider>/<model>`, and `voice` when the TTS says. Whatever
 * a plugin doesn't say is left out.
 */
export function agentConfig(session: LiveKitSession): AgentConfig {
  const out: AgentConfig = {};
  for (const role of ["llm", "stt", "tts"] as const) {
    let part: LiveKitModel | null | undefined;
    try {
      part = session[role];
    } catch {
      continue;
    }
    if (!part) continue;
    const name = modelName(part.provider, part.model);
    if (name) out[role] = name;
    if (role === "tts" && typeof part.voice === "string" && part.voice) out.voice = part.voice;
  }
  return out;
}

function modelName(provider: unknown, model: unknown): string | null {
  const m = typeof model === "string" && !["", "unknown"].includes(model.toLowerCase()) ? model : null;
  const p = typeof provider === "string" && !["", "unknown", "livekit"].includes(provider.toLowerCase()) ? provider : null;
  if (!m) return null;
  return m.includes("/") || !p ? m : `${p.toLowerCase()}/${m}`;
}

/**
 * Map a LiveKit `ChatContext` (`session.history`), or its `toJSON({excludeTimestamp: false})`, to sopops turns and
 * tool calls. A message that repeats the one before it is dropped (LiveKit can add a user transcript twice).
 */
export function fromLiveKit(history: LiveKitSession["history"] | { items: Item[] }): { turns: Turn[]; toolCalls: ToolCall[] } {
  const data = ("toJSON" in history ? history.toJSON({ excludeTimestamp: false }) : history) as { items?: Item[] };
  const turns: Turn[] = [];
  const tools = new Map<string, ToolCall>();
  for (const item of data.items ?? []) {
    const at = item.createdAt ?? item.created_at; // milliseconds in Node; sopops reads either
    const callId = item.callId ?? item.call_id;
    if (item.type === "message") {
      const text = (item.content ?? []).filter((c: unknown) => typeof c === "string").join(" ").trim();
      const prev = turns[turns.length - 1];
      if (prev && prev.role === item.role && prev.text === text && prev.name === undefined) continue;
      turns.push({ role: item.role, text, at, interrupted: Boolean(item.interrupted) });
    } else if (item.type === "function_call") {
      const args = item.args ?? item.arguments;
      turns.push({ role: "tool", text: `${item.name}(${args ?? ""})`, name: item.name, at });
      tools.set(callId, { name: item.name, arguments: args, call_id: callId, at });
    } else if (item.type === "function_call_output" && tools.has(callId)) {
      Object.assign(tools.get(callId)!, { result: item.output, is_error: Boolean(item.isError ?? item.is_error) });
    }
  }
  return { turns, toolCalls: [...tools.values()] };
}

/**
 * Post `session.history` to sopops when the job shuts down, keyed by the job id (rooms get reused), with the
 * release. Errors are logged, never thrown into shutdown.
 */
export function recordSession(ctx: LiveKitJobContext, session: LiveKitSession, options: RecordSessionOptions): void {
  const startedAt = new Date().toISOString();
  ctx.addShutdownCallback(async () => {
    try {
      const { turns, toolCalls } = fromLiveKit(session.history);
      await options.sopops.postSession(
        {
          platform: "livekit",
          external_id: ctx.job.id,
          agent: options.agent ?? (ctx.job.agentName || null),
          release: options.release ?? null,
          started_at: startedAt,
          ended_at: new Date().toISOString(),
          turns,
          tool_calls: toolCalls,
          metadata: { room: ctx.room.name },
          test: options.test ?? null,
          config: { ...agentConfig(session), ...(options.config ?? {}) },
        },
        // stay inside the job's shutdown budget
        { retries: 1, timeoutMs: 3000 },
      );
    } catch (e) {
      console.error(`sopops: couldn't send session ${ctx.job.id}: ${e instanceof Error ? e.message : String(e)}`);
    }
  });
}
