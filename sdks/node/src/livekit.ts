/**
 * LiveKit Agents (Node) adapter: fetch the agent's instructions at call start, and post each finished session to
 * sopops when the job shuts down.
 *
 *   const session = new voice.AgentSession({ ... });
 *   const ins = await attach(ctx, session, { sopops });
 *   await session.start({ agent: voice.Agent.create({ instructions: ins.text }), room: ctx.room });
 *
 * `attach` does both halves; `recordSession` is the lower-level half for code that already has its instructions.
 * This package doesn't depend on `@livekit/agents`; the shapes below are the parts of `JobContext` and
 * `voice.AgentSession` (v1) it reads.
 */
import { releasePin, SopOps } from "./index.js";
import type { AgentConfig, Instructions, SessionIn } from "./index.js";

type Item = Record<string, any>;
type Turn = NonNullable<SessionIn["turns"]>[number];
type ToolCall = NonNullable<SessionIn["tool_calls"]>[number];

/** The parts of `JobContext` used here. */
export interface LiveKitJobContext {
  job: { id: string; agentName?: string; metadata?: Metadata };
  room: { name?: string; metadata?: Metadata };
  addShutdownCallback(callback: () => Promise<void>): void;
}

/** Job or room metadata: LiveKit hands it over as a JSON string, but an object is read too. */
type Metadata = string | Record<string, unknown> | null;

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

export interface AttachOptions {
  /** The client to use. Default: a new `SopOps()` (`SOPOPS_URL` / `SOPOPS_TOKEN`). */
  sopops?: SopOps;
  /** The LiveKit agent identifier, such as its name or phone number. Default: the job's agent name. */
  agent?: string;
  /** Pin a release; default: a pin in the job's or room's metadata, else the live release. */
  release?: number | null;
  /** Text to use if sopops is down and nothing is cached (see `SopOps.fetchInstructions`). */
  fallback?: string;
  /** Values for `{{placeholders}}` only this call knows, like the time or the caller. They're filled here, in your
   * process, so they never reach sopops. One you pass no value for is left as it is and listed in `ins.unfilled`. */
  variables?: Record<string, string | number | boolean>;
  /** An A/B test this call is part of. */
  test?: string | null;
  /** Settings to report beyond the models read from the session (`agentConfig`); these win. */
  config?: AgentConfig;
}

export interface RecordSessionOptions {
  sopops: SopOps;
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

/** A release pinned for this call in the job's dispatch metadata, else the room's metadata. */
export function pinnedRelease(ctx: LiveKitJobContext): number | null {
  return releasePin(ctx.job?.metadata) ?? releasePin(ctx.room?.metadata);
}

/**
 * Fetch the agent's instructions and post the session at shutdown: `fetchInstructions` and `recordSession` in one
 * call, the way the Python adapter's `attach` does it.
 *
 *   const ins = await attach(ctx, session, { sopops, fallback: FALLBACK });
 *
 * `agent` is a LiveKit agent identifier and defaults to the job's agent name. `release` pins one, and without it a
 * release pinned in the job's or room's metadata is honored, else the live one. Call it after `ctx.connect()` for
 * room metadata.
 * A sopops outage never blocks the call: the fetch falls back to the last good copy, then `fallback` (see
 * `SopOps.fetchInstructions`), and the post at shutdown logs its errors instead of throwing into shutdown.
 */
export async function attach(ctx: LiveKitJobContext, session: LiveKitSession, options: AttachOptions = {}): Promise<Instructions> {
  const sopops = options.sopops ?? new SopOps();
  const agent = options.agent ?? ctx.job.agentName;
  if (!agent) throw new Error("pass agent when the LiveKit job has no agent name");
  const release = options.release ?? pinnedRelease(ctx);
  const ins = await sopops.fetchPlatformInstructions("livekit", agent, { release, fallback: options.fallback, variables: options.variables });
  recordSession(ctx, session, { sopops, agent: ins.agent, release: ins.release, test: options.test, config: options.config });
  return ins;
}
