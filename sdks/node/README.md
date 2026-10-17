# sopops-client (TypeScript)

The TypeScript client for [sopops](../../README.md): fetch an agent's instructions when a call starts, and send each finished session back. ESM with types, no runtime dependencies; Node 18+, browsers and edge runtimes (it only needs `fetch`).

It's not on npm yet; build it from a clone (`pnpm -C sdks/node install && pnpm -C sdks/node build`) and install the folder.

## ElevenLabs

```ts
import { Conversation } from "@elevenlabs/client";
import { Sopops } from "sopops-client";
import { elevenLabsSession } from "sopops-client/elevenlabs";

const sopops = new Sopops({ url: SOPOPS_URL, token: SOPOPS_TOKEN });
const s = await elevenLabsSession(sopops, { agent: "front-desk" });   // or { agentId: "<ElevenLabs agent id>" }
const conversation = await Conversation.startSession({ ...s.sessionOptions, onMessage: console.log });
```

`sessionOptions` is `{agentId, overrides: {agent: {prompt: {prompt}}}, dynamicVariables, onConnect}`: the compiled prompt as a client-side override, `sopops_release` and `sopops_agent` as dynamic variables, and an `onConnect` that reports the conversation id to sopops so the post-call session links to the release it ran on. With your own `onConnect`, call `s.link(conversationId)` from it. `@elevenlabs/react` takes the same options in `startSession`.

**Turn on overrides first:** in ElevenLabs, the agent's Security tab → Overrides → System prompt. Without it, ElevenLabs refuses the session.

**Tokens in browsers.** A token with the `runtime` scope can read the workspace's prompts (and with `ingest`, send sessions). Give a browser a runtime-only token. In a web app, build the options on your server and pass them to the page, or put sopops behind your own auth. For a browser to call sopops directly, set `SOPOPS_CORS_ORIGINS` on the server.

Phone calls don't run this code: for those, sopops answers ElevenLabs' conversation-initiation webhook, or pushes the prompt to the agent on publish (see the main README).

## Any framework

```ts
const sopops = new Sopops();                                         // SOPOPS_URL, SOPOPS_TOKEN (Node)
const ins = await sopops.fetchInstructions("front-desk", { release: null, fallback: "You are a helpful assistant." });
ins.text; ins.release; ins.blocks;

await sopops.postSession({ platform: "api", external_id: callId, agent: ins.agent, release: ins.release,
                           turns: [{ role: "user", text: "Hi", offset_s: 0.4 }] });
```

**A sopops outage never blocks a call.** The fetch gives up after `timeoutMs` (2000 by default), then returns the last good copy (`ins.source === "cache"`), else your `fallback`, else throws `SopopsUnavailable`. The default cache is in memory; on Node, `new FileCache()` from `sopops-client/node` keeps it on disk (`SOPOPS_CACHE_DIR`, default `~/.cache/sopops`). `releasePin(metadata)` reads a pinned release (`{"sopops_release": 3}`) for A/B tests. `postSession` retries network errors, 429 and 5xx; resending a session is safe.

A LiveKit adapter for Node isn't here yet; LiveKit agents in Python use `sopops_client.livekit` ([sdks/python](../python)).

## Development

```sh
pnpm install && pnpm typecheck && pnpm test && pnpm build
```
