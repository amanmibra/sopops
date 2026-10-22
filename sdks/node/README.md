# sopops (TypeScript)

The TypeScript client for [sopops](../../README.md): fetch an agent's instructions when a call starts, and send each finished session back. ESM with types, no runtime dependencies; Node 18+, browsers and edge runtimes (it only needs `fetch`).

```sh
npm install sopops        # pnpm add sopops, yarn add sopops, bun add sopops
```

To work on the client itself, build a clone instead: `pnpm -C sdks/node install && pnpm -C sdks/node build`.

## ElevenLabs

```ts
import { Conversation } from "@elevenlabs/client";
import { SopOps } from "sopops";
import { elevenLabsSession } from "sopops/elevenlabs";

const sopops = new SopOps({ url: SOPOPS_URL, token: SOPOPS_TOKEN });
const s = await elevenLabsSession(sopops, { agent: "front-desk" });   // or { agentId: "<ElevenLabs agent id>" }
const conversation = await Conversation.startSession({ ...s.sessionOptions, onMessage: console.log });
```

`sessionOptions` is `{agentId, overrides: {agent: {prompt: {prompt}}}, dynamicVariables, onConnect}`: the agent's full instructions as a client-side override, `sopops_release` and `sopops_agent` as dynamic variables, and an `onConnect` that reports the conversation id to sopops so the post-call session links to the release it ran on. With your own `onConnect`, call `s.link(conversationId)` from it. `@elevenlabs/react` takes the same options in `startSession`.

**Turn on overrides first:** in ElevenLabs, the agent's Security tab → Overrides → System prompt (ElevenLabs' name for the instructions). Without it, ElevenLabs refuses the session.

**Tokens in browsers.** A token with the `runtime` scope can read the workspace's instructions (and with `ingest`, send sessions). Give a browser a runtime-only token. In a web app, build the options on your server and pass them to the page, or put sopops behind your own auth. For a browser to call sopops directly, set `SOPOPS_CORS_ORIGINS` on the server.

Phone calls don't run this code: for those, sopops answers ElevenLabs' conversation-initiation webhook, or pushes the instructions to the agent on publish (see the main README).

## Any framework

```ts
const sopops = new SopOps();                                         // SOPOPS_URL, SOPOPS_TOKEN (Node)
const ins = await sopops.fetchInstructions("front-desk", { release: null, fallback: "You are a helpful assistant." });
ins.text; ins.release; ins.blocks;

await sopops.postSession({ platform: "api", external_id: callId, agent: ins.agent, release: ins.release,
                           turns: [{ role: "user", text: "Hi", offset_s: 0.4 }] });
```

Without a client to keep, one call does the fetch:

```ts
import { fetchInstructions } from "sopops";

const ins = await fetchInstructions("front-desk");        // the live release
const ins = await fetchInstructions("front-desk", 14);    // pinned, e.g. one arm of an A/B test
```

It reads the same environment variables and falls back the same way, and builds a client per call, so keep a `SopOps`
in anything hot. (Python's is `sopops.fetch(agent, release)`; this one keeps the longer name so it doesn't shadow the
global `fetch`.)

**A sopops outage never blocks a call.** The fetch gives up after `timeoutMs` (2000 by default), then returns the last good copy (`ins.source === "cache"`), else your `fallback`, else throws `SopOpsUnavailable`. The default cache is in memory; on Node, `new FileCache()` from `sopops/node` keeps it on disk (`SOPOPS_CACHE_DIR`, default `~/.cache/sopops`). `releasePin(metadata)` reads a pinned release (`{"sopops_release": 3}`) for A/B tests. `postSession` retries network errors, 429 and 5xx; resending a session is safe.

## Streaming (optional)

```ts
const s = await elevenLabsSession(sopops, { agent: "sakura-sushi", stream: true });
const conversation = await Conversation.startSession({ ...s.sessionOptions });
```

With `stream: true`, `sessionOptions` also has `onMessage`, `onAgentToolResponse` and `onDisconnect`: each message and tool result goes to sopops as it happens, in batches, keyed by the conversation id, and when the conversation ends the full conversation is posted, replacing what was streamed. Nothing blocks the conversation or throws into it: if sopops is slow or down, or the queue fills, events are logged and dropped. To add your own handler for one of these, call ours from it; `s.end()` stops streaming and posts. Streaming needs a token with the `ingest` scope. `onGuidance` is accepted but not yet used: it's reserved for runtime guidance in a later version.

Anywhere else, `sopops.stream(...)` returns a `SessionStream`:

```ts
const stream = sopops.stream({ platform: "api", external_id: callId, agent: "tonys-pizza", release: ins.release });
stream.turn({ role: "user", text: "Delivery please.", at: new Date().toISOString() });
stream.toolCall({ name: "check_delivery_zone", arguments: { address: "221 Mercer St" }, result: { in_zone: true } });
await stream.close();                                // sends what's queued (at most 1s), then stops
await sopops.postSession(fullSession);
```

`openSession` and `appendEvents` are the two requests underneath (`POST …/sessions/open`, `POST …/sessions/<platform>/<id>/events`).

## LiveKit

```ts
import { recordSession } from "sopops/livekit";

const ins = await sopops.fetchInstructions(`livekit:${ctx.job.agentName}`, { fallback: FALLBACK });
const session = new voice.AgentSession({ ... });
recordSession(ctx, session, { sopops, agent: ins.agent, release: ins.release });
await session.start({ agent: voice.Agent.create({ instructions: ins.text }), room: ctx.room });
```

`recordSession` posts `session.history` to sopops when the job shuts down, with timestamps and tool calls, keyed by the job id, with the release. Errors are logged, never thrown into shutdown. It has no `stream` option yet; the Python adapter does. `@livekit/agents` isn't a dependency: only the parts of `JobContext` and `AgentSession` it reads are typed here.

A complete agent is in [examples/livekit-restaurant](../../examples/livekit-restaurant) (Python), and a web call in [examples/elevenlabs-web](../../examples/elevenlabs-web).

## Development

```sh
pnpm install && pnpm typecheck && pnpm test && pnpm build
```
