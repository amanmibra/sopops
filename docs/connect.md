# Connect your agent

Each agent gets its instructions one of two ways, set per agent under the gear icon → Delivery:

- **Pull:** the agent, or its platform's call-start webhook, fetches the live release when a call starts.
- **Push:** publishing, or making a release live, writes the instructions into the agent on its platform, with results and retry on the release.

| Platform | Instructions | Sessions back |
|---|---|---|
| **LiveKit** | Pull with the Python SDK's `attach` (or `fetchInstructions` in TypeScript) | No code: connect the LiveKit Cloud project and add its webhook, and sopops joins each call and records it live (no tool calls). Or `attach`, which posts `session.history` with tool calls at shutdown and can stream it; `recordSession` in TypeScript |
| **ElevenLabs** | Push (`conversation_config.agent.prompt.prompt`), the conversation-initiation webhook for phone calls, or client overrides from the TypeScript SDK | No code: the post-call webhook, with tool calls; on Enterprise, phone calls also stream live from the monitor socket. Or the TypeScript SDK's `elevenLabsSession` (posted, optionally streamed) |
| **Vapi** | Push (the system message in `model.messages`), or the assistant-request webhook | No code: the same Server URL takes `end-of-call-report` (with tool calls), and `conversation-update` and `transcript` live |
| **Retell** | Later: agents can be marked Retell, nothing is delivered yet | |
| **Anything else** | `GET …/agents/<agent>` with a runtime token, or either SDK | `POST …/sessions`, or `sopops import FILE.jsonl` |

**Python** (`pip install "./sdks/python[livekit]"`, [sdks/python](../sdks/python); `SOPOPS_URL` is the workspace's URL, `http://sopops:8484/v1/workspaces/<ws>`, and `SOPOPS_TOKEN` a token with the `runtime` and `ingest` scopes):

```python
from sopops_client.livekit import attach

async def entrypoint(ctx: JobContext):
    await ctx.connect()
    session = AgentSession(...)
    ins = await attach(ctx, session, agent="livekit:tonys-pizza", fallback=FALLBACK, stream=True)
    await session.start(room=ctx.room, agent=Agent(instructions=ins.text, tools=TOOLS))
```

`attach` fetches the instructions (a release pinned as `{"sopops_release": 3}` in job or room metadata wins), notes the release in `session.userdata`, streams turns and tool calls while the call runs (`stream=True`, optional), and posts the full session at shutdown. If sopops is down it uses the last good copy on disk, then `fallback`, so it never blocks a call. Elsewhere: `Sopops().fetch_instructions(agent)` and `post_session(...)`. A complete agent: [examples/livekit-restaurant](../examples/livekit-restaurant).

**TypeScript** ([sdks/node](../sdks/node); ESM, no dependencies, Node 18+, browsers and edge runtimes):

```ts
import { Sopops } from "sopops-client";
import { elevenLabsSession } from "sopops-client/elevenlabs";

const sopops = new Sopops({ url: SOPOPS_URL, token: SOPOPS_TOKEN });
const s = await elevenLabsSession(sopops, { agent: "front-desk", stream: true });
const conversation = await Conversation.startSession({ ...s.sessionOptions });

const ins = await sopops.fetchInstructions("front-desk", { fallback: "You are a helpful assistant." });   // any framework
```

**Webhooks.** ElevenLabs phone calls: the conversation-initiation webhook at `…/runtime/elevenlabs/conversation-init`, with the webhook secret as `X-Sopops-Secret` (and, under Security → Overrides, the System prompt override allowed). Vapi: the phone number's Server URL at `…/runtime/vapi/assistant-request?agent=<id>`, with the secret as a Bearer Token credential.

**Sessions with no code.** Set up under the gear icon → Agents and platforms → Platforms. Each one feeds the same ingest and streaming as the SDKs, and a session that streamed and then arrives whole (a post-call webhook, an end-of-call report) stays one session: the full post wins.

- *LiveKit.* Connect the project (URL, an API key and its secret; `pip install 'sopops[livekit]'` where sopops and its worker run), then in LiveKit Cloud → Settings → Webhooks add `…/capture/livekit/webhook`, signed with that key. When an agent joins a room, sopops joins it hidden and subscribe-only, reads the transcript the agent publishes on `lk.transcription`, and streams it into a session keyed by the job id (as `attach` keys it), closed when the call ends. The agent is the name it was dispatched with, else `{"sopops": {"agent": "<id>"}}` in job or room metadata, else the mapping in Settings. Tool calls aren't in the room, so they aren't recorded this way.
- *ElevenLabs.* Add a post-call webhook (transcripts) at `…/capture/elevenlabs/post-call` and paste its HMAC secret into Settings. On Enterprise, turn on live monitoring (and Monitoring in the agent's Advanced settings): each call the conversation-initiation webhook serves is followed on the monitor socket with the API key. Where that's refused, it falls back to the post-call webhook, and Settings says why.
- *Vapi.* On the assistant, under Server Messages, turn on `end-of-call-report`, `conversation-update` and `transcript` beside `assistant-request`. Same auth as the instructions.

Webhooks need sopops reachable from the internet: for local testing, a tunnel such as `cloudflared tunnel --url http://127.0.0.1:8484`, and open the UI through the tunnel's address so Settings shows the right webhook URLs.

**Tokens.** The gear icon → Tokens, each with the scopes it needs: `runtime` (fetching instructions, call-start webhooks) and `ingest` (sessions). Shown once, stored hashed, can expire or be revoked. Once a workspace has a runtime token, fetching instructions needs one; without one it serves instructions openly, for local use. Platform keys and webhook secrets are encrypted with `SOPOPS_SECRET_KEY` and never returned.
