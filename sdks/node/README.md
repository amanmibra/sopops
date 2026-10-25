# sopops (TypeScript)

The TypeScript client for [sopops](../../README.md). It fetches an agent's instructions when a call starts, and sends each finished session back.

ESM with types, no runtime dependencies. It runs on Node 18+, in browsers and on edge runtimes, and needs only `fetch`.

```sh
npm install sopops        # pnpm add sopops, yarn add sopops, bun add sopops
```

Set `SOPOPS_URL` to the workspace's API URL and `SOPOPS_TOKEN` to a token from Settings → Tokens.

## LiveKit

One call fetches the instructions and posts the session when the job shuts down.

```ts
import { attach } from "sopops/livekit";

async function entrypoint(ctx: JobContext) {
  await ctx.connect();
  const session = new voice.AgentSession({ ... });
  const ins = await attach(ctx, session, { fallback: FALLBACK });
  await session.start({ room: ctx.room, agent: new voice.Agent({ instructions: ins.text }) });
}
```

Call `attach` after `ctx.connect()` so it can read the room's metadata. The agent is the job's LiveKit agent name unless you pass a raw LiveKit identifier in `agent`. `attach` notes the release in `session.userData` (`sopops_release`). If sopops is down, `attach` returns the last good copy, then your `fallback`, so it never blocks a call. With `stream: true` it also sends each turn and tool call as it happens; the full session posted at shutdown still replaces what was streamed.

## ElevenLabs

```ts
import { elevenLabsSession } from "sopops/elevenlabs";

const s = await elevenLabsSession(sopops, { agent: "agent_123" });   // the ElevenLabs agent id
const conversation = await Conversation.startSession({ ...s.sessionOptions });
```

On a server, `s.initiationData` is the answer to ElevenLabs's conversation initiation webhook. `stream: true` streams the conversation and posts it when it ends.

## Any framework

```ts
import { SopOps } from "sopops";

const sopops = new SopOps();                     // SOPOPS_URL, SOPOPS_TOKEN
const ins = await sopops.fetchInstructions("front-desk", { fallback: "You are a helpful assistant." });

await sopops.postSession({ platform: "api", external_id: callId, agent: ins.agent, release: ins.release,
                           turns: [{ role: "user", text: "Hi", offset_s: 0.4 }] });
```

`ins.text` is the instructions, `ins.release` the release they came from, `ins.blocks` the blocks they were built from. Without a client to keep, the exported `fetchInstructions("front-desk")` does the fetch on its own. `variables` fills per-call `{{placeholders}}` in your process, and `sopops.stream(...)` sends a session's turns as the call runs.

The Python client in [sdks/python](../python) has the same surface, in snake_case. The onboarding MCP server, `sopops-mcp`, ships with the Python package and runs with `uvx`, so TypeScript projects use it the same way.

## More

| | |
|---|---|
| [Connect your agent](https://docs.sopops.dev/connect) | ElevenLabs with `elevenLabsSession`, Vapi, webhooks, no-code capture |
| [Tokens and scopes](https://docs.sopops.dev/connect#manage-tokens-and-secrets) | `runtime` to fetch instructions, `ingest` to send sessions |
| [Streaming](https://docs.sopops.dev/api/sessions) | `sopops.stream(...)` sends turns and tool calls as the call runs |
| [A/B tests](https://docs.sopops.dev/ab-tests) | `test` and `config` on `attach`, and pinning a release |
| [Configuration](https://docs.sopops.dev/configuration) | Every environment variable |

A complete agent is in [examples/livekit-restaurant](../../examples/livekit-restaurant) (Python), and a web call in [examples/elevenlabs-web](../../examples/elevenlabs-web).

## Reference

| | |
|---|---|
| [`FileCache`, `SOPOPS_CACHE_DIR`](https://docs.sopops.dev/sdk#where-the-last-good-copy-is-kept) | The last good instructions on disk (`sopops/node`); the default is in memory |
| [`releasePin(metadata)`](https://docs.sopops.dev/ab-tests#what-you-can-pass) | A release pinned in job or room metadata |
| [`s.link`, `@elevenlabs/react`](https://docs.sopops.dev/connect#connect-elevenlabs) | Your own `onConnect`, and React |
| [Browsers](https://docs.sopops.dev/connect#connect-elevenlabs) | Runtime-only tokens, `SOPOPS_CORS_ORIGINS` |
| [`onGuidance`](https://docs.sopops.dev/api/sessions#streaming-optional) | Accepted, not called yet |

## Development

```sh
pnpm install && pnpm typecheck && pnpm test && pnpm build
```
