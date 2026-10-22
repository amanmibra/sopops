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

Call `attach` after `ctx.connect()` so it can read the room's metadata. The agent is `livekit:<the job's agent name>` unless you pass `agent`. If sopops is down, `attach` returns the last good copy, then your `fallback`, so it never blocks a call.

## Any framework

```ts
import { SopOps } from "sopops";

const sopops = new SopOps();                     // SOPOPS_URL, SOPOPS_TOKEN
const ins = await sopops.fetchInstructions("front-desk", { fallback: "You are a helpful assistant." });

await sopops.postSession({ platform: "api", external_id: callId, agent: ins.agent, release: ins.release,
                           turns: [{ role: "user", text: "Hi", offset_s: 0.4 }] });
```

`ins.text` is the instructions, `ins.release` the release they came from, `ins.blocks` the blocks they were built from. Without a client to keep, the exported `fetchInstructions("front-desk")` does the fetch on its own.

## More

| | |
|---|---|
| [Connect your agent](https://docs.sopops.dev/connect) | ElevenLabs with `elevenLabsSession`, Vapi, webhooks, no-code capture |
| [Tokens and scopes](https://docs.sopops.dev/connect#manage-tokens-and-secrets) | `runtime` to fetch instructions, `ingest` to send sessions |
| [Streaming](https://docs.sopops.dev/api/sessions) | `sopops.stream(...)` sends turns and tool calls as the call runs |
| [A/B tests](https://docs.sopops.dev/ab-tests) | `test` and `config` on `attach`, and pinning a release |
| [Configuration](https://docs.sopops.dev/configuration) | Every environment variable |

A complete agent is in [examples/livekit-restaurant](../../examples/livekit-restaurant) (Python), and a web call in [examples/elevenlabs-web](../../examples/elevenlabs-web).

## Development

```sh
pnpm install && pnpm typecheck && pnpm test && pnpm build
```
