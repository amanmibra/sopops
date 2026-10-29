# Connect your agent

Agents get their instructions from sopops and send their sessions back. Paths below start with `/v1/workspaces/{ws}`, where `{ws}` is the workspace id.

| Platform | Instructions | Sessions |
|---|---|---|
| LiveKit | Code: `attach` | No code (room capture), or code: `attach`, with tool calls |
| ElevenLabs | No code: push on publish, or the conversation-initiation webhook | No code: the post-call webhook |
| Vapi | No code: push on publish, or the assistant-request webhook | No code: the end-of-call report |

You can set up sessions without instructions. An agent can keep its instructions in its own code and still send every call back with no code.

## Install the client

```sh
pip install "sopops[livekit]"    # Python, or plain sopops without the LiveKit adapter
npm install sopops               # TypeScript: pnpm add sopops, yarn add sopops, bun add sopops
```

The Python package is [sdks/python](../sdks/python). The TypeScript one ([sdks/node](../sdks/node)) is ESM, has no dependencies, and runs on Node 18+, in browsers and on edge runtimes. Every function and type in both is in the [SDK reference](sdk.md).

Both SDKs read these variables. This page's REST calls need none of them and work with `curl`.

| Variable | Value |
|---|---|
| `SOPOPS_URL` | The workspace's API URL, `https://sopops.dev/api/v1/workspaces/<id>`. |
| `SOPOPS_TOKEN` | Settings → Access: `runtime` to fetch instructions, `ingest` to send sessions. |
| `SOPOPS_CACHE_DIR` | Where the last good instructions are kept on disk. Default `~/.cache/sopops`. |

Set them wherever the agent runs. On LiveKit Cloud, add `SOPOPS_URL` and `SOPOPS_TOKEN` as the agent's secrets (Agents → your agent → Secrets, or the `lk` CLI's secrets options) and deploy again; the SDK can't reach sopops without them.

Python caches on disk already. TypeScript caches in memory, so pass `FileCache` from `sopops/node` to keep the copy on disk, and a restarted process still has one when sopops is unreachable.

```python
from sopops import SopOps

sopops = SopOps()                                       # disk, or cache_dir="/var/cache/sopops"
```

```ts
import { SopOps } from "sopops";
import { FileCache } from "sopops/node";                // Node only

const sopops = new SopOps({ cache: new FileCache() });  // or new FileCache("/var/cache/sopops")
```

## How sopops knows which agent

| Name | What it is | Who picks it |
|---|---|---|
| sop id | The readable name in sopops's URLs, like `copper-wok-harbor`. | sopops. |
| platform identifier | The raw value your platform uses for the agent, such as a LiveKit agent name, a database row id, or a phone number. | You. |
| platform ref | The stored platform and identifier, such as `livekit:42`. | sopops. |

- Pass the raw platform identifier to an adapter. `attach` resolves it within
  LiveKit, so you do not add `livekit:` yourself.
- A generic instruction fetch can take a sop id, platform ref, or a raw
  identifier. Scope a raw identifier to its platform when another platform can
  use the same value.

## Choose pull or push

Set it once per platform, in that platform's card under the gear icon → Agents and platforms. LiveKit agents always pull, with `attach`.

| Delivery | What happens |
|---|---|
| Pull | The agent, or its platform's call-start webhook, fetches the live release when a call starts. |
| Push | Publishing, or making a release live, writes the instructions into the agent on its platform. The release page shows each push's result and lets you retry it. |

Session capture without code sits under the gear icon → Agents and platforms → Platforms, on the same ingest and streaming as the SDKs. A session that streams and then arrives whole stays one session; the full copy replaces what streamed.

## Connect LiveKit

**Instructions:** the LiveKit adapter's `attach`, in either SDK. LiveKit has no call-start webhook, so instructions need `attach`. The token needs the `runtime` and `ingest` scopes, and on LiveKit Cloud both variables go in the agent's secrets (above). With `attach`, the LiveKit webhook below isn't needed: it's for recording calls without code. To only record calls, skip to **Sessions with no code** below.

```python
from sopops.livekit import attach

async def entrypoint(ctx: JobContext):
    await ctx.connect()
    session = AgentSession(...)
    ins = await attach(ctx, session, agent="tonys-pizza", fallback=FALLBACK, stream=True)
    await session.start(room=ctx.room, agent=Agent(instructions=ins.text, tools=TOOLS))
```

```ts
import { attach } from "sopops/livekit";

async function entrypoint(ctx: JobContext) {
  await ctx.connect();
  const session = new voice.AgentSession({ ... });
  const ins = await attach(ctx, session, { agent: "tonys-pizza", fallback: FALLBACK });
  await session.start({ room: ctx.room, agent: new voice.Agent({ instructions: ins.text, tools: TOOLS }) });
}
```

| `attach` | What it does |
|---|---|
| Fetches instructions | The live release, or one pinned as `{"sopops_release": 3}` in job or room metadata. Notes which in `session.userdata`. |
| Never blocks a call | If sopops is down, the last good copy, then `fallback`. |
| Posts the session | `session.history`, with tool calls, at shutdown. Python can also send each turn and tool call as the call runs (`stream=True`). |
| Reports the version | LLM, speech and voice models, so Insights can compare them ([A/B tests](ab-tests.md)). |
| Runtime variables win | Pass `variables={"now": ..., "caller": ...}` to fill <code v-pre>{{placeholders}}</code> in your own process, so those values never reach sopops. They win over values set in sopops, which fill the rest; a placeholder with neither is served as it is. What is left is in `ins.unfilled`, and a mismatch logs a warning instead of stopping the call. |

- Record a session you start some other way with `record_session`, or `recordSession` in TypeScript.
- Outside LiveKit, use `fetch_instructions(agent)` and `post_session(...)` on a `SopOps`, `fetchInstructions` and `postSession` in TypeScript, or `sopops.fetch(agent, release)` for Python that isn't async ([SDK reference](sdk.md)).
- A complete agent is in [examples/livekit-restaurant](../examples/livekit-restaurant).

**Sessions with no code** (no tool calls):

1. In Settings, connect the project: URL, an API key and its secret.
2. Self-hosted without Docker only: run `pip install 'sopops-server[livekit]'` where sopops and its worker run. The Docker image already has it.
3. In LiveKit Cloud → Settings → Webhooks, add `/v1/workspaces/{ws}/capture/livekit/webhook`, signed with that key.

- sopops joins each room an agent joins, hidden and subscribe-only, and streams `lk.transcription` into a session keyed by the job id, closed when the call ends, as `attach` does.
- The agent is the dispatch name, else `{"sopops": {"agent": "<id>"}}` in job or room metadata, else the mapping in Settings.
- Only LiveKit Cloud URLs work, unless `SOPOPS_LIVEKIT_ANY_HOST` is set ([configuration](configuration.md)).
- sopops doesn't know which release the call ran unless it is pinned as `{"sopops_release": 3}` in job or room metadata.

## Connect ElevenLabs

**Instructions**, any of:

- Push to `conversation_config.agent.prompt.prompt`.
- Client overrides from the TypeScript SDK.
- For phone calls, the conversation-initiation webhook at `/v1/workspaces/{ws}/runtime/elevenlabs/conversation-init`, with the webhook secret as `X-SopOps-Secret`. Allow the System prompt override under Security → Overrides.

**Sessions with no code** (with tool calls):

- Add a post-call webhook (transcripts) at `/v1/workspaces/{ws}/capture/elevenlabs/post-call` and paste its HMAC secret into Settings.
- To stream phone calls live, on Enterprise, turn on live monitoring and Monitoring in the agent's Advanced settings. sopops follows each call the conversation-initiation webhook serves on the monitor socket, using the API key.
- A refused socket falls back to the post-call webhook, and Settings says why.

**Sessions from code:** `elevenLabsSession` in TypeScript or `elevenlabs_session` in Python (posted, optionally streamed) fetches the instructions and builds the `startSession` options that carry them, so a browser call runs the live release. A complete page is in [examples/elevenlabs-web](../examples/elevenlabs-web). The Python SDK's `elevenlabs_session` does the same for the `elevenlabs` package, and both return `initiation_data` / `initiationData` for a server that answers ElevenLabs's conversation initiation webhook ([SDK reference](sdk.md#the-elevenlabs-adapter)).

```ts
import { SopOps } from "sopops";
import { elevenLabsSession } from "sopops/elevenlabs";

const sopops = new SopOps({ url: SOPOPS_URL, token: SOPOPS_TOKEN });
const s = await elevenLabsSession(sopops, { agent: "agent_123", stream: true });   // the ElevenLabs agent id
const conversation = await Conversation.startSession({ ...s.sessionOptions });
```

```python
from sopops import SopOps
from sopops.elevenlabs import elevenlabs_session

sopops = SopOps(url=SOPOPS_URL, token=SOPOPS_TOKEN)
s = await elevenlabs_session(sopops, "agent_123", stream=True)                     # the ElevenLabs agent id
```

- `@elevenlabs/react` takes the same options: `useConversation().startSession({ ...s.sessionOptions })`.
- The `onConnect` in `s.sessionOptions` reports the conversation id, so the post-call session links to the release it ran on. Pass your own and call `s.link(conversationId)` from it.

**In a browser:** a `runtime` token reads every instruction in the workspace, and with `ingest` it sends sessions.

- Give a page a runtime-only token, build the options on your server and pass them to the page, or put sopops behind your own auth.
- A browser calls sopops directly only if the server sets [`SOPOPS_CORS_ORIGINS`](configuration.md#core) to the page's origin.

## Connect Vapi

**Instructions:** push to the system message in `model.messages`. Or set the phone number's Server URL to `/v1/workspaces/{ws}/runtime/vapi/assistant-request?agent={id}`, with the secret as a Bearer Token credential.

**Sessions with no code:** on the assistant, under Server Messages, turn on `end-of-call-report` (with tool calls), `conversation-update` and `transcript` (live) beside `assistant-request`. Auth is the same.

## Bring in calls from before you connected

Once the API key is saved, Settings → Platforms shows **Backfill the last 7 days** under ElevenLabs and Vapi ([API reference](api/sessions.md#backfill-from-elevenlabs-or-vapi)).

- It counts each agent's finished conversations, or each assistant's ended calls, from those 7 days first, with what checking the new ones costs once the team has spent something.
- It then brings them in as the same sessions the post-call webhook and the end-of-call report make.
- Anything already in sopops is skipped, so you can run it again.

## Connect Retell

Coming later. You can mark agents as Retell, but nothing is delivered yet.

## Connect anything else

- **Instructions:** `GET /v1/workspaces/{ws}/agents/{agent}` with a runtime token, or either SDK, in any framework.
- **Sessions:** `POST /v1/workspaces/{ws}/sessions`, or `sopops import FILE.jsonl`.

```python
from sopops import SopOps

ins = await SopOps().fetch_instructions("front-desk", fallback="You are a helpful assistant.")
```

```ts
import { SopOps } from "sopops";

const ins = await new SopOps().fetchInstructions("front-desk", { fallback: "You are a helpful assistant." });
```

## Test webhooks locally

Webhooks need sopops reachable from the internet. Run a tunnel, then open the UI at the tunnel's address so Settings shows the right webhook URLs.

```sh
cloudflared tunnel --url http://127.0.0.1:8484
```

## Manage tokens and secrets

- Create tokens under the gear icon → Tokens. Scopes: `runtime` for fetching instructions and call-start webhooks, `ingest` for sessions.
- Tokens are shown once, stored hashed, and can expire or be revoked.
- Once a workspace has a runtime token, fetching instructions needs one. Until then sopops serves them openly, for local use.
- Platform keys and webhook secrets are encrypted with `SOPOPS_SECRET_KEY` and never returned.
