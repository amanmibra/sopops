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
 */
import type { FetchOptions, Instructions, Sopops } from "./index.js";

/** What `startSession` takes from us: `agentId`, `overrides.agent.prompt.prompt`, `dynamicVariables`, `onConnect`. */
export interface ElevenLabsSessionOptions {
  agentId: string;
  overrides: { agent: { prompt: { prompt: string } } };
  dynamicVariables: Record<string, string | number | boolean>;
  onConnect: (props: { conversationId: string }) => void;
}

export interface ElevenLabsSession {
  instructions: Instructions;
  /** Spread into `startSession`. If you pass your own `onConnect`, call `link(conversationId)` from it. */
  sessionOptions: ElevenLabsSessionOptions;
  /** Report the conversation id to sopops so the post-call session links to this release. Never throws. */
  link(conversationId: string): Promise<void>;
}

export interface ElevenLabsSessionInput extends FetchOptions {
  /** The sopc agent id or platform ref. Default: `elevenlabs:<agentId>`. */
  agent?: string;
  /** The ElevenLabs agent id. Default: from the sopops agent's platform ref. */
  agentId?: string;
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

  const link = async (conversationId: string) => {
    if (instructions.release == null || !conversationId) return;
    try {
      await sopops.recordServed({ platform: "elevenlabs", callId: conversationId, agent: instructions.agent, release: instructions.release });
    } catch (e) {
      console.warn(`sopops: couldn't record the release for conversation ${conversationId}: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return {
    instructions,
    link,
    sessionOptions: {
      agentId,
      overrides: { agent: { prompt: { prompt: instructions.text } } },
      dynamicVariables,
      onConnect: ({ conversationId }) => void link(conversationId),
    },
  };
}

function splitRef(ref: string | null): [string | undefined, string | undefined] {
  if (!ref) return [undefined, undefined];
  const i = ref.indexOf(":");
  return i < 0 ? [undefined, undefined] : [ref.slice(0, i), ref.slice(i + 1)];
}
