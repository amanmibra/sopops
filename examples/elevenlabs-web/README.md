# 📋🗄️ Example: an ElevenLabs web call, prompt from sopops

Sakura Sushi's host, as a voice call in the browser on ElevenLabs. The page fetches the agent's prompt from sopops when the call starts and hands it to ElevenLabs as a client-side override, so a publish in sopops reaches the next call without touching the agent in the ElevenLabs dashboard.

```
elevenlabs-web/
  index.html        a Call button and the transcript
  src/main.ts       fetches the prompt, starts the ElevenLabs conversation with it
  package.json      @elevenlabs/client, and sopops from ../../sdks/node
  .env.example      sopops settings (VITE_ variables)
```

## How the page uses it

```ts
// src/main.ts
const s = await elevenLabsSession(sopops, { agent: "agent_123", stream });
const ours = s.sessionOptions;
conversation = await Conversation.startSession({
  ...ours,
  onMessage: (m) => { ours.onMessage?.(m); show(m.role, m.message); },
  ...
});
```

`elevenLabsSession`, from the [TypeScript client](../../sdks/node), fetches the live release's prompt for the ElevenLabs agent and returns `startSession` options:

| Option | What it carries |
|---|---|
| `agentId` | The ElevenLabs agent to call. sopops reads it from the matching agent's platform ref. |
| `overrides.agent.prompt.prompt` | The compiled prompt, replacing the one in the ElevenLabs dashboard for this call |
| `dynamicVariables` | `sopops_agent` and `sopops_release` |
| `onConnect` | Reports the conversation id to sopops (`POST …/runtime/served`), so the session ElevenLabs sends later links to the release it ran on |

The page shows which prompt it got, e.g. `sakura-sushi, release 4 (live)`. If sopops can't be reached, the call starts with the last good copy fetched since the page loaded (`cache`; in the browser the cache is in memory), and without one, `elevenLabsSession` throws and the page shows the error. Pass `fallback:` to start with your own text instead.

**Turn on overrides first:** in ElevenLabs, the agent's Security tab → Overrides → System prompt. Without it, ElevenLabs refuses the session.

The page adds its own `onMessage`, `onAgentToolResponse` and `onDisconnect` to draw the transcript. Each one calls ours first, so streaming (below) keeps working; `onConnect` isn't overridden, so ours runs as is.

## Streaming (optional)

With `VITE_SOPOPS_STREAM=1`, the page passes `stream: true`, and `sessionOptions` gets `onMessage`, `onAgentToolResponse` and `onDisconnect` of its own: each message and tool result goes to sopops as it happens, in batches, keyed by the conversation id, and the call shows on the Sessions page while it's still going. When the call ends, the whole conversation is posted. Streaming never blocks the call or throws into it; if sopops is slow or down, events are dropped and logged to the console.

Streaming sends sessions, so the token needs the `ingest` scope as well as `runtime`.

## Run it

Start sopops (the [Quickstart](../../README.md#quickstart)) with `SOPOPS_CORS_ORIGINS=http://localhost:5173`, so the page can call it. Publish the restaurant instructions into a workspace named `prod` (as in the [LiveKit example](../livekit-restaurant#run-it)), and make a token with the `runtime` scope (plus `ingest` to stream) in Settings → Tokens.

In ElevenLabs, create an agent for Sakura Sushi and turn on the system prompt override. Its dashboard prompt doesn't matter: sopops replaces it on every call. Import that agent into sopops with an `elevenlabs: <agent id>` platform ref, then set `VITE_ELEVENLABS_AGENT` to the same id.

```sh
pnpm -C ../../sdks/node install && pnpm -C ../../sdks/node build    # the client isn't on npm yet
cp .env.example .env.local     # VITE_SOPOPS_URL, VITE_SOPOPS_TOKEN, VITE_ELEVENLABS_AGENT
pnpm install
pnpm dev                       # http://localhost:5173
```

Press **Call**, allow the microphone, and ask for a table for four tonight.

## Tokens in production

Vite puts every `VITE_` variable into the page, so this setup hands the token to anyone who opens it. That's fine on your machine and not otherwise. In production, build the options on your server (`elevenLabsSession` runs on Node too) and send the page `agentId`, `overrides` and `dynamicVariables`; report the conversation id back through your server; and stream, if you do, from your server as well. A token in a browser should only ever have the `runtime` scope.

Phone calls to an ElevenLabs agent don't run this code: for those, sopops answers ElevenLabs' conversation-initiation webhook, or pushes the prompt to the agent on publish (see [Connect your agent](../../README.md#connect-your-agent)).
