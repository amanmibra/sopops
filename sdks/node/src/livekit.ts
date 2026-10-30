/**
 * LiveKit Agents (Node) adapter: fetch the agent's instructions at call start, and post each finished session to
 * sopops when the job shuts down.
 *
 *   const session = new voice.AgentSession({ ... });
 *   const ins = await attach(ctx, session, { sopops });
 *   await session.start({ agent: voice.Agent.create({ instructions: ins.text }), room: ctx.room });
 *
 * `attach` does both halves; `recordSession` is the lower-level half for code that already has its instructions.
 * With `stream: true` each turn and tool call is also sent as it happens.
 * This package doesn't depend on `@livekit/agents`; the shapes below are the parts of `JobContext` and
 * `voice.AgentSession` (v1) it reads.
 */
import { releasePin, SopOps } from "./index.js";
import type { AgentConfig, Guidance, Instructions, SessionIn, SessionStream } from "./index.js";

type Item = Record<string, any>;
type Turn = NonNullable<SessionIn["turns"]>[number];
type ToolCall = NonNullable<SessionIn["tool_calls"]>[number];

/** The parts of `JobContext` used here. */
export interface LiveKitJobContext {
  job: { id: string; agentName?: string; metadata?: Metadata };
  room: { name?: string; metadata?: Metadata };
  addShutdownCallback(callback: (reason?: string) => Promise<void>): void;
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
  /** `attach` notes `sopops_release` and `sopops_agent` here. LiveKit throws reading it before it's set. */
  userData?: unknown;
  /** The session's events; `stream: true` listens for `conversation_item_added` and `function_tools_executed`. */
  on?(event: any, listener: (ev: any) => void): unknown;
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
  /** Labels for this call (`["pilot"]`), added to the agent's tags in sopops. */
  tags?: string[];
  /** Also send each turn and tool call while the call runs (see `recordSession`). Default false. */
  stream?: boolean;
  /** Not yet used: reserved for runtime guidance in a later version. */
  onGuidance?: (guidance: Guidance) => void;
}

export interface RecordSessionOptions {
  /** The client to use. Default: a new `SopOps()` (`SOPOPS_URL` / `SOPOPS_TOKEN`). */
  sopops?: SopOps;
  /** The sop agent id. Default: the one `attach` noted in `session.userData`, else the job's agent name. */
  agent?: string | null;
  /** The release the call ran on (`Instructions.release`). Default: the one `attach` noted in `session.userData`. */
  release?: number | null;
  /** Also open the session now and send each turn and tool call as it happens. Default false. */
  stream?: boolean;
  /** Not yet used: reserved for runtime guidance in a later version. */
  onGuidance?: (guidance: Guidance) => void;
  /** An A/B test this call is part of. */
  test?: string | null;
  /** Settings to report beyond the models read from the session (`agentConfig`); these win. */
  config?: AgentConfig;
  /** Labels for this call (`["pilot"]`), added to the agent's tags in sopops. */
  tags?: string[];
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
  return m.includes("/") || !p ? m : `${providerName(p)}/${m}`;
}

/** Some plugins report their API host as the provider (`api.anthropic.com`): keep the company's name. */
function providerName(provider: string): string {
  const host = provider.toLowerCase().split("://").pop()!.split("/")[0]!;
  const labels = host.split(".").filter(Boolean);
  return labels.length >= 2 ? labels[labels.length - 2]! : host;
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
      const turn = messageTurn(item, at);
      if (!repeats(turns[turns.length - 1], turn)) turns.push(turn);
    } else if (item.type === "function_call") {
      const args = item.args ?? item.arguments;
      turns.push(toolTurn(item.name, args, at));
      tools.set(callId, { name: item.name, arguments: args, call_id: callId, at });
    } else if (item.type === "function_call_output" && tools.has(callId)) {
      Object.assign(tools.get(callId)!, { result: item.output, is_error: Boolean(item.isError ?? item.is_error) });
    }
  }
  return { turns, toolCalls: [...tools.values()] };
}

function messageTurn(item: Item, at: unknown): Turn {
  const text = (item.content ?? []).filter((c: unknown) => typeof c === "string").join(" ").trim();
  return { role: item.role, text, at: at as Turn["at"], interrupted: Boolean(item.interrupted) };
}

function toolTurn(name: string, args: unknown, at: unknown): Turn {
  return { role: "tool", text: `${name}(${args ?? ""})`, name, at: at as Turn["at"] };
}

/** LiveKit can add a user transcript twice. */
function repeats(prev: Turn | undefined, turn: Turn): boolean {
  return Boolean(prev && prev.role === turn.role && prev.text === turn.text && prev.name === undefined);
}

const RELEASE_KEY = "sopops_release";
const AGENT_KEY = "sopops_agent";

function userData(session: LiveKitSession): unknown {
  try {
    return session.userData;
  } catch {
    return undefined; // LiveKit throws when no userData was set
  }
}

function noted(session: LiveKitSession, key: string): unknown {
  const u = userData(session);
  return u && typeof u === "object" ? (u as Record<string, unknown>)[key] : undefined;
}

/** Note the release and agent in `session.userData`, as the Python adapter does in `session.userdata`. */
function remember(session: LiveKitSession, ins: Instructions): void {
  const u = userData(session);
  try {
    if (u == null) session.userData = { [RELEASE_KEY]: ins.release, [AGENT_KEY]: ins.agent };
    else if (typeof u === "object") Object.assign(u, { [RELEASE_KEY]: ins.release, [AGENT_KEY]: ins.agent });
  } catch {
    console.warn(`sopops: can't note the release on the session's userData; add a ${RELEASE_KEY} field to it`);
  }
}

/**
 * Post `session.history` to sopops when the job shuts down, keyed by the job id (rooms get reused), with the
 * release. Errors are logged, never thrown into shutdown.
 *
 * With `stream: true` it also opens the session now and sends each turn and tool call as it happens, in batches
 * (see `SessionStream`); it never blocks or throws into the agent. The full post at shutdown still happens and
 * replaces what was streamed.
 */
export function recordSession(ctx: LiveKitJobContext, session: LiveKitSession, options: RecordSessionOptions = {}): void {
  const sopops = options.sopops ?? new SopOps();
  const startedAt = new Date().toISOString();
  const agent = () => options.agent ?? ((noted(session, AGENT_KEY) as string | undefined) || ctx.job.agentName || null);
  const release = () => options.release ?? ((noted(session, RELEASE_KEY) as number | null | undefined) ?? null);
  const tags = () => ({ test: options.test ?? null, config: { ...agentConfig(session), ...(options.config ?? {}) }, tags: options.tags ?? [] });
  const live = options.stream ? liveStream(ctx, session, sopops, { platform: "livekit", external_id: ctx.job.id, channel: "voice", agent: agent(), release: release(), started_at: startedAt, metadata: { room: ctx.room.name }, ...tags() }, options.onGuidance) : undefined;
  ctx.addShutdownCallback(async (reason?: string) => {
    if (live) await live.close();
    try {
      const { turns, toolCalls } = fromLiveKit(session.history);
      await sopops.postSession(
        {
          platform: "livekit",
          external_id: ctx.job.id,
          agent: agent(),
          release: release(),
          started_at: startedAt,
          ended_at: new Date().toISOString(),
          turns,
          tool_calls: toolCalls,
          metadata: { room: ctx.room.name, shutdown_reason: reason ?? "" },
          ...tags(),
        },
        // stay inside the job's shutdown budget
        { retries: 1, timeoutMs: 3000 },
      );
    } catch (e) {
      console.error(`sopops: couldn't send session ${ctx.job.id}: ${e instanceof Error ? e.message : String(e)}`);
    }
  });
}

/** Open a stream for the job and feed it from the session's `conversation_item_added` and `function_tools_executed` events. */
function liveStream(ctx: LiveKitJobContext, session: LiveKitSession, sopops: SopOps, open: Parameters<SopOps["stream"]>[0], onGuidance: ((g: Guidance) => void) | undefined): SessionStream {
  const live = sopops.stream(open, { onGuidance });
  if (!session.on) {
    console.warn(`sopops: session ${ctx.job.id} has no events to stream; it's posted at shutdown`);
    return live;
  }
  let last: Turn | undefined; // the last turn sent, to drop repeats like fromLiveKit does
  session.on("conversation_item_added", (ev: { item?: Item }) => {
    try {
      const item = ev.item!;
      if ((item.type ?? "message") !== "message") return;
      const turn = messageTurn(item, item.createdAt ?? item.created_at);
      if (repeats(last, turn)) return;
      last = turn;
      live.turn(turn);
    } catch (e) {
      console.warn(`sopops: couldn't stream a conversation item: ${e instanceof Error ? e.message : String(e)}`);
    }
  });
  session.on("function_tools_executed", (ev: { functionCalls?: Item[]; functionCallOutputs?: Array<Item | undefined> }) => {
    try {
      const outputs = ev.functionCallOutputs ?? [];
      (ev.functionCalls ?? []).forEach((call, i) => {
        const at = call.createdAt ?? call.created_at;
        const args = call.args ?? call.arguments;
        const callId = call.callId ?? call.call_id;
        last = toolTurn(call.name, args, at);
        live.turn(last);
        const toolCall: ToolCall = { name: call.name, arguments: args, call_id: callId, at };
        const out = outputs[i];
        if (out) Object.assign(toolCall, { result: out.output, is_error: Boolean(out.isError ?? out.is_error) });
        live.toolCall(toolCall);
      });
    } catch (e) {
      console.warn(`sopops: couldn't stream a tool call: ${e instanceof Error ? e.message : String(e)}`);
    }
  });
  return live;
}

/** A release pinned for this call in the job's dispatch metadata, else the room's metadata. */
export function pinnedRelease(ctx: LiveKitJobContext): number | null {
  return releasePin(ctx.job?.metadata) ?? releasePin(ctx.room?.metadata);
}

/**
 * Fetch the agent's instructions, note the release in `session.userData`, and post the session at shutdown:
 * `fetchPlatformInstructions` and `recordSession` in one call, as the Python adapter's `attach` does it.
 *
 *   const ins = await attach(ctx, session, { sopops, fallback: FALLBACK });
 *
 * `agent` is a LiveKit agent identifier and defaults to the job's agent name. `release` pins one, and without it a
 * release pinned in the job's or room's metadata is honored, else the live one. Call it after `ctx.connect()` for
 * room metadata. `stream: true` also sends each turn and tool call while the call runs.
 * A sopops outage never blocks the call: the fetch falls back to the last good copy, then `fallback` (see
 * `SopOps.fetchInstructions`), and the post at shutdown logs its errors instead of throwing into shutdown.
 */
export async function attach(ctx: LiveKitJobContext, session: LiveKitSession, options: AttachOptions = {}): Promise<Instructions> {
  const sopops = options.sopops ?? new SopOps();
  const agent = options.agent ?? ctx.job.agentName;
  if (!agent) throw new Error("pass agent when the LiveKit job has no agent name");
  const release = options.release ?? pinnedRelease(ctx);
  const ins = await sopops.fetchPlatformInstructions("livekit", agent, { release, fallback: options.fallback, variables: options.variables });
  remember(session, ins);
  recordSession(ctx, session, { sopops, agent: ins.agent, release: ins.release, test: options.test, config: options.config, tags: options.tags, stream: options.stream, onGuidance: options.onGuidance });
  return ins;
}
