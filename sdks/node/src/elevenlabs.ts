/**
 * ElevenLabs adapter: the compiled prompt as client-side overrides for `@elevenlabs/client`
 * (`Conversation.startSession`) or `@elevenlabs/react` (`useConversation().startSession`).
 *
 *   const s = await elevenLabsSession(sopops, { agent: "tonys-pizza" });
 *   const conversation = await Conversation.startSession({ ...s.sessionOptions, onMessage });
 *
 * The agent must allow overriding its system prompt (ElevenLabs: the agent's Security tab → Overrides),
 * or ElevenLabs rejects the session. This package doesn't depend on `@elevenlabs/client`; the shapes below
 * match its `SessionConfig` (v1.28).
 *
 * With `stream: true`, the conversation's messages and tool results also go to sopops as they happen, and the
 * full conversation is posted when it ends (a token with the `ingest` scope is needed).
 */
import type { AgentConfig, FetchOptions, Guidance, Instructions, SessionIn, SessionStream, Sopops } from "./index.js";

type Turn = NonNullable<SessionIn["turns"]>[number];
type ToolCall = NonNullable<SessionIn["tool_calls"]>[number];

/** The parts of `@elevenlabs/client`'s `onAgentToolResponse` payload we read. */
export interface ElevenLabsToolResponse {
  tool_name: string;
  tool_call_id: string;
  is_error: boolean;
  full_tool_result?: string;
}

/**
 * What `startSession` takes from us: `agentId`, `overrides.agent.prompt.prompt`, `dynamicVariables`, `onConnect`,
 * and with `stream: true`, `onMessage`, `onAgentToolResponse` and `onDisconnect`. To add your own handler for any
 * of these, call ours from it.
 */
export interface ElevenLabsSessionOptions {
  agentId: string;
  overrides: { agent: { prompt: { prompt: string } } };
  dynamicVariables: Record<string, string | number | boolean>;
  onConnect: (props: { conversationId: string }) => void;
  onMessage?: (props: { message: string; role: "user" | "agent" }) => void;
  onAgentToolResponse?: (props: ElevenLabsToolResponse) => void;
  onDisconnect?: (details: { reason: string }) => void;
}

export interface ElevenLabsSession {
  instructions: Instructions;
  /** Spread into `startSession`. If you pass your own `onConnect`, call `link(conversationId)` from it. */
  sessionOptions: ElevenLabsSessionOptions;
  /** Report the conversation id to sopops so the post-call session links to this release. Never throws. */
  link(conversationId: string): Promise<void>;
  /** With `stream: true`: stop streaming and post the full conversation (`onDisconnect` calls it). Never throws; repeat calls return the same promise. */
  end(reason?: string): Promise<void>;
}

export interface ElevenLabsSessionInput extends FetchOptions {
  /** The sopc agent id or platform ref. Default: `elevenlabs:<agentId>`. */
  agent?: string;
  /** The ElevenLabs agent id. Default: from the sopops agent's platform ref. */
  agentId?: string;
  /** Send messages and tool results to sopops as they happen, and the full conversation when it ends. Default false. */
  stream?: boolean;
  /** An A/B test this conversation is part of (sent as the dynamic variable `sopops_test`). */
  test?: string;
  /** Settings to report (`{ version: "v2" }`), sent as `sopops_<key>` dynamic variables. sopops reads the agent's
   *  LLM, voice and TTS model itself, with the API key in Settings. */
  config?: Record<string, string | number | boolean>;
  /** Not yet used: reserved for runtime guidance in a later version. */
  onGuidance?: (guidance: Guidance) => void;
}

/** Fetch the agent's instructions and build the `startSession` options that carry them. */
export async function elevenLabsSession(sopops: Sopops, input: ElevenLabsSessionInput): Promise<ElevenLabsSession> {
  const agent = input.agent ?? (input.agentId ? `elevenlabs:${input.agentId}` : undefined);
  if (!agent) throw new Error("pass agent (the sopops agent) or agentId (the ElevenLabs agent id)");
  const instructions = await sopops.fetchInstructions(agent, input);
  const [platform, fromRef] = splitRef(instructions.platformRef);
  const agentId = input.agentId ?? (platform === "elevenlabs" ? fromRef : undefined);
  if (!agentId) throw new Error(`sopops agent '${instructions.agent}' isn't an ElevenLabs agent; pass agentId`);

  const dynamicVariables: Record<string, string | number | boolean> = { sopops_agent: instructions.agent };
  if (instructions.release != null) dynamicVariables.sopops_release = instructions.release;
  if (input.test) dynamicVariables.sopops_test = input.test;
  for (const [k, v] of Object.entries(input.config ?? {})) dynamicVariables[`sopops_${k}`] = v;

  const link = async (conversationId: string) => {
    if (instructions.release == null || !conversationId) return;
    try {
      await sopops.recordServed({ platform: "elevenlabs", callId: conversationId, agent: instructions.agent, release: instructions.release });
    } catch (e) {
      console.warn(`sopops: couldn't record the release for conversation ${conversationId}: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const sessionOptions: ElevenLabsSessionOptions = {
    agentId,
    overrides: { agent: { prompt: { prompt: instructions.text } } },
    dynamicVariables,
    onConnect: ({ conversationId }) => void link(conversationId),
  };
  if (!input.stream) return { instructions, link, sessionOptions, end: async () => {} };

  const recorder = new Recorder(sopops, instructions, input.onGuidance, { test: input.test ?? null, config: input.config ?? {} });
  return {
    instructions,
    link,
    end: (reason) => recorder.end(reason),
    sessionOptions: {
      ...sessionOptions,
      onConnect: ({ conversationId }) => {
        void link(conversationId);
        recorder.connect(conversationId);
      },
      onMessage: ({ message, role }) => recorder.turn({ role, text: message, at: now() }),
      onAgentToolResponse: (r) => recorder.tool(r),
      onDisconnect: ({ reason }) => void recorder.end(reason),
    },
  };
}

/** Keeps the conversation as it happens, streams it once the conversation id is known, and posts it at the end. */
class Recorder {
  private turns: Turn[] = [];
  private toolCalls: ToolCall[] = [];
  private conversationId: string | undefined;
  private stream: SessionStream | undefined;
  private ended: Promise<void> | undefined;
  private readonly startedAt = now();

  constructor(
    private readonly sopops: Sopops,
    private readonly instructions: Instructions,
    private readonly onGuidance: ((guidance: Guidance) => void) | undefined,
    private readonly tags: { test: string | null; config: AgentConfig },
  ) {}

  connect(conversationId: string): void {
    if (this.stream || !conversationId) return;
    this.conversationId = conversationId;
    this.stream = this.sopops.stream(
      { platform: "elevenlabs", external_id: conversationId, channel: "voice", agent: this.instructions.agent, release: this.instructions.release, started_at: this.startedAt, ...this.tags },
      { onGuidance: this.onGuidance },
    );
    for (const t of this.turns) this.stream.turn(t); // anything said before the id was known
    for (const c of this.toolCalls) this.stream.toolCall(c);
  }

  turn(turn: Turn): void {
    if (this.ended) return;
    this.turns.push(turn);
    this.stream?.turn(turn);
  }

  tool(r: ElevenLabsToolResponse): void {
    if (this.ended) return;
    const at = now();
    const call: ToolCall = { name: r.tool_name, call_id: r.tool_call_id, is_error: r.is_error, at };
    if (r.full_tool_result !== undefined) call.result = r.full_tool_result;
    this.turn({ role: "tool", text: r.tool_name, name: r.tool_name, at });
    this.toolCalls.push(call);
    this.stream?.toolCall(call);
  }

  end(reason = ""): Promise<void> {
    this.ended ??= this.post(reason);
    return this.ended;
  }

  private async post(reason: string): Promise<void> {
    if (!this.stream || !this.conversationId) return; // never connected: nothing to key the session by
    await this.stream.close();
    try {
      await this.sopops.postSession({
        platform: "elevenlabs",
        external_id: this.conversationId,
        channel: "voice",
        agent: this.instructions.agent,
        release: this.instructions.release,
        started_at: this.startedAt,
        ended_at: now(),
        turns: this.turns,
        tool_calls: this.toolCalls,
        metadata: { disconnect_reason: reason },
        ...this.tags,
      });
    } catch (e) {
      console.warn(`sopops: couldn't send conversation ${this.conversationId}: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
}

function now(): string {
  return new Date().toISOString();
}

function splitRef(ref: string | null): [string | undefined, string | undefined] {
  if (!ref) return [undefined, undefined];
  const i = ref.indexOf(":");
  return i < 0 ? [undefined, undefined] : [ref.slice(0, i), ref.slice(i + 1)];
}
