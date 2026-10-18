# sopops-client

The Python client for [sopops](../../README.md): fetch an agent's instructions when a call starts, and send each finished session back.

```sh
pip install "sopops-client[livekit]"     # or plain sopops-client without the LiveKit adapter
```

It's not on PyPI yet; install from a clone with `pip install "./sdks/python[livekit]"`. The import name is `sopops_client` (the `sopops` name is the server).

## LiveKit

```python
from livekit.agents import Agent, AgentSession, JobContext
from sopops_client.livekit import attach

async def entrypoint(ctx: JobContext):
    await ctx.connect()
    session = AgentSession(...)                                   # your STT, LLM, TTS
    ins = await attach(ctx, session, agent="tonys-pizza")         # SOPOPS_URL, SOPOPS_TOKEN
    await session.start(room=ctx.room, agent=Agent(instructions=ins.text))
```

`attach` fetches the instructions, notes the release in `session.userdata` (`sopops_release`), and at shutdown posts the session to sopops: `session.history` with timestamps and tool calls, keyed by the job id. To pin a release for a call (A/B tests), put `{"sopops_release": 3}` in the job's dispatch metadata or the room's metadata.

## Any framework

```python
from sopops_client import Sopops

sopops = Sopops()                                    # SOPOPS_URL, SOPOPS_TOKEN
ins = await sopops.fetch_instructions("tonys-pizza", release=None, fallback="You are a helpful host.")
ins.text, ins.release, ins.blocks                    # the prompt, its release, the files it was built from

await sopops.post_session({"platform": "api", "external_id": call_id, "agent": "tonys-pizza", "release": ins.release,
                           "turns": [{"role": "user", "text": "Hi", "offset_s": 0.4}]})
```

| | |
|---|---|
| `SOPOPS_URL` | The workspace's API URL, e.g. `http://sopops.internal:8484/v1/workspaces/prod`. |
| `SOPOPS_TOKEN` | A workspace token (sopops Settings → Tokens) with the scopes the agent needs: `runtime` to fetch prompts, `ingest` to send sessions. `SOPOPS_INGEST_TOKEN` is read too. A workspace without a runtime token serves prompts without one. |
| `SOPOPS_CACHE_DIR` | Where the last good instructions are kept, default `~/.cache/sopops`. |

**A sopops outage never blocks a call.** The fetch gives up after `timeout` (2s by default). Then it returns the last good copy for that agent and release from disk (`ins.source == "cache"`), else your `fallback` (`"fallback"`), else raises `SopopsUnavailable`. Sessions are retried on connection errors, 429 and 5xx; sending the same session again is safe.

## Streaming (optional)

```python
ins = await attach(ctx, session, agent="tonys-pizza", stream=True)
```

With `stream=True`, `attach` opens the session when the call starts and sends each turn and tool call as it happens (LiveKit's `conversation_item_added` and `function_tools_executed` events), in batches from a background task. It never blocks the agent and never raises into it: if sopops is slow or down, or the queue fills, events are logged and dropped. At shutdown it still posts the full session, which replaces what was streamed, so the default (`stream=False`, one post at the end) loses nothing. `on_guidance=` is accepted but not yet used: it's reserved for runtime guidance in a later version.

Outside LiveKit, use `SessionStream` directly:

```python
from sopops_client import SessionStream

stream = SessionStream(sopops, {"platform": "api", "external_id": call_id, "agent": "tonys-pizza", "release": ins.release})
stream.start()
stream.turn({"role": "user", "text": "Delivery please.", "at": time.time()})
stream.tool_call({"name": "check_delivery_zone", "arguments": {"address": "221 Mercer St"}, "result": {"in_zone": True}})
await stream.aclose()                                # sends what's queued (at most 1s), then stops
await sopops.post_session(full_session)
```

`Sopops.open_session` and `Sopops.append_events` are the two requests underneath (`POST …/sessions/open`, `POST …/sessions/<platform>/<id>/events`). Streaming needs the `ingest` scope, like posting.

The session format is in [API.md](../../API.md#the-canonical-format). Adapters for other frameworks go in their own modules next to `sopops_client.livekit`, each importing its framework only when used. The TypeScript client is in [sdks/node](../node). A complete LiveKit agent is in [examples/livekit-restaurant](../../examples/livekit-restaurant).

## Development

```sh
cd sdks/python && uv run pytest
```
