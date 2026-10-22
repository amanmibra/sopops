# Connect your agent

Agents get their instructions from sopops and send their sessions back. Paths below start with `/v1/workspaces/{ws}`, where `{ws}` is the workspace id.

## Install the client

```sh
pip install "sopops[livekit]"    # Python, or plain sopops without the LiveKit adapter
npm install sopops               # TypeScript: pnpm add sopops, yarn add sopops, bun add sopops
```

Both read `SOPOPS_URL` (the workspace's API URL, `https://app.sopops.dev/v1/workspaces/<id>`) and `SOPOPS_TOKEN`
(Settings → Tokens: `runtime` to fetch instructions, `ingest` to send sessions). Neither is needed to read this
page's REST calls, which work with `curl`.

## How sopops knows which agent

Each agent has two names. Its **sopc id** is the readable one in sopops's URLs (`copper-wok-harbor`). Its
**identifier** is what your code asks for at call start: `platform` plus `platform_id`, written `livekit:<id>`.

On LiveKit the identifier is yours to choose: a LiveKit agent name, a row id from your database, whatever you
already use to tell locations apart. On ElevenLabs and Vapi it has to be that platform's own id, so pushes and
webhooks match. Any of three names works when you fetch: the sopc id, `livekit:42`, or `42` on its own, so code that already
holds a location id can pass it straight through. sopops keeps identifiers unique in a workspace, and says so
rather than guessing if two agents on different platforms share one.

## Choose pull or push

Set this per agent under the gear icon → Delivery.

- **Pull:** the agent, or its platform's call-start webhook, fetches the live release when a call starts.
- **Push:** publishing, or making a release live, writes the instructions into the agent on its platform. The release page shows each push's result and lets you retry it.

Set up session capture without code under the gear icon → Agents and platforms → Platforms. It uses the same ingest and streaming as the SDKs. A session that streams during a call and then arrives whole stays one session, and the full copy replaces what streamed.

## Connect LiveKit

**Instructions:** the Python SDK's `attach` (`fetchInstructions` in TypeScript). Install with `pip install "./sdks/python[livekit]"` ([sdks/python](../sdks/python)). Set `SOPOPS_URL` to the workspace URL and `SOPOPS_TOKEN` to a token with the `runtime` and `ingest` scopes.

```python
from sopops.livekit import attach

async def entrypoint(ctx: JobContext):
    await ctx.connect()
    session = AgentSession(...)
    ins = await attach(ctx, session, agent="livekit:tonys-pizza", fallback=FALLBACK, stream=True)
    await session.start(room=ctx.room, agent=Agent(instructions=ins.text, tools=TOOLS))
```

`attach` fetches the instructions (a release pinned as `{"sopops_release": 3}` in job or room metadata wins) and notes the release in `session.userdata`. It streams turns and tool calls during the call (`stream=True`, optional) and posts `session.history`, with tool calls, at shutdown (`recordSession` in TypeScript). If sopops is down, it uses the last good copy on disk, then `fallback`, so it never blocks a call. It also reports the session's LLM, speech and voice models, so Insights can compare versions ([A/B tests](ab-tests.md)). Outside LiveKit, use `SopOps().fetch_instructions(agent)` and `post_session(...)`, or `sopops.fetch(agent, release)` where the code isn't async (`fetchInstructions(agent, release)` in TypeScript). A `{{placeholder}}` sopops has no value for is served as it is, so your code can fill it at call start: `attach(..., variables={"now": ..., "caller": ...})`, or `variables` on `fetch_instructions` (`fetchInstructions(agent, { variables })` in TypeScript). That happens in your process, so those values never reach sopops; what's left over is in `ins.unfilled`, and anything that doesn't line up logs a warning instead of stopping the call. A complete agent is in [examples/livekit-restaurant](../examples/livekit-restaurant).

**Sessions with no code** (no tool calls):

1. In Settings, connect the project: URL, an API key and its secret.
2. Run `pip install 'sopops-server[livekit]'` where sopops and its worker run.
3. In LiveKit Cloud → Settings → Webhooks, add `/v1/workspaces/{ws}/capture/livekit/webhook`, signed with that key.

sopops joins each room an agent joins, hidden and subscribe-only. It streams the transcript from `lk.transcription` into a session keyed by the job id (as `attach` does), closed when the call ends. The agent is the dispatch name, else `{"sopops": {"agent": "<id>"}}` in job or room metadata, else the mapping in Settings.

## Connect ElevenLabs

**Instructions**, any of:

- Push to `conversation_config.agent.prompt.prompt`.
- Client overrides from the TypeScript SDK.
- For phone calls, the conversation-initiation webhook at `/v1/workspaces/{ws}/runtime/elevenlabs/conversation-init`, with the webhook secret as `X-SopOps-Secret`. Allow the System prompt override under Security → Overrides.

**Sessions with no code** (with tool calls): add a post-call webhook (transcripts) at `/v1/workspaces/{ws}/capture/elevenlabs/post-call` and paste its HMAC secret into Settings. On Enterprise, turn on live monitoring (and Monitoring in the agent's Advanced settings) to stream phone calls live. sopops follows each call the conversation-initiation webhook serves on the monitor socket, using the API key. If that's refused, it falls back to the post-call webhook, and Settings says why.

**Sessions from code:** the TypeScript SDK's `elevenLabsSession` (posted, optionally streamed).

**Calls from before you connected:** once the API key is saved, Settings, then Platforms, shows **Backfill the last 7 days** under ElevenLabs. It counts each agent's finished conversations from the last 7 days first (and what checking the new ones costs, once the team has spent something), then brings them in as sessions, the same ones the post-call webhook makes. Conversations already in sopops are skipped, so you can run it again. See [the API](api/sessions.md#backfill-from-elevenlabs-or-vapi).

## Connect Vapi

**Instructions:** push to the system message in `model.messages`. Or set the phone number's Server URL to `/v1/workspaces/{ws}/runtime/vapi/assistant-request?agent={id}`, with the secret as a Bearer Token credential.

**Sessions with no code:** on the assistant, under Server Messages, turn on `end-of-call-report` (with tool calls), `conversation-update` and `transcript` (live) beside `assistant-request`. Auth is the same.

**Calls from before you connected:** once the API key is saved, Settings, then Platforms, shows **Backfill the last 7 days** under Vapi. It counts each assistant's ended calls from the last 7 days, then brings them in as sessions, the same ones the end-of-call report makes. Calls already in sopops are skipped.

## Connect Retell

Coming later. You can mark agents as Retell, but nothing is delivered yet.

## Connect anything else

- **Instructions:** `GET /v1/workspaces/{ws}/agents/{agent}` with a runtime token, or either SDK.
- **Sessions:** `POST /v1/workspaces/{ws}/sessions`, or `sopops import FILE.jsonl`.

## Use the TypeScript SDK

[sdks/node](../sdks/node): ESM, no dependencies, Node 18+, browsers and edge runtimes.

```ts
import { SopOps } from "sopops";
import { elevenLabsSession } from "sopops/elevenlabs";

const sopops = new SopOps({ url: SOPOPS_URL, token: SOPOPS_TOKEN });
const s = await elevenLabsSession(sopops, { agent: "front-desk", stream: true });
const conversation = await Conversation.startSession({ ...s.sessionOptions });

const ins = await sopops.fetchInstructions("front-desk", { fallback: "You are a helpful assistant." });   // any framework
```

## Test webhooks locally

Webhooks need sopops reachable from the internet. Run a tunnel, then open the UI at the tunnel's address so Settings shows the right webhook URLs.

```sh
cloudflared tunnel --url http://127.0.0.1:8484
```

## Manage tokens and secrets

Create tokens under the gear icon → Tokens. Scopes: `runtime` for fetching instructions and call-start webhooks, `ingest` for sessions. Tokens are shown once, stored hashed, and can expire or be revoked.

Once a workspace has a runtime token, fetching instructions needs one. Until then, sopops serves instructions openly, for local use. Platform keys and webhook secrets are encrypted with `SOPOPS_SECRET_KEY` and never returned.
