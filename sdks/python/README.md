# sopops

The Python client for [sopops](../../README.md): fetch an agent's instructions when a call starts, and send each finished session back.

```sh
pip install "sopops[livekit]"     # or plain sopops without the LiveKit adapter
pip install "sopops[mcp]"         # sopops-mcp, the onboarding MCP server for Claude Code or Codex
```

It's not on PyPI yet; install from a clone with `pip install "./sdks/python[livekit]"`. The server is a separate package, `sopops-server`.

## LiveKit

```python
from livekit.agents import Agent, AgentSession, JobContext
from sopops.livekit import attach

async def entrypoint(ctx: JobContext):
    await ctx.connect()
    session = AgentSession(...)                                   # your STT, LLM, TTS
    ins = await attach(ctx, session, agent="tonys-pizza")         # SOPOPS_URL, SOPOPS_TOKEN
    await session.start(room=ctx.room, agent=Agent(instructions=ins.text))
```

`attach` fetches the instructions, notes the release in `session.userdata` (`sopops_release`), and at shutdown posts the session to sopops: `session.history` with timestamps and tool calls, keyed by the job id. To pin a release for a call (A/B tests), put `{"sopops_release": 3}` in the job's dispatch metadata or the room's metadata.

## Any framework

```python
from sopops import SopOps

sopops = SopOps()                                    # SOPOPS_URL, SOPOPS_TOKEN
ins = await sopops.fetch_instructions("tonys-pizza", release=None, fallback="You are a helpful host.")
ins.text, ins.release, ins.blocks                    # the instructions, their release, the files they were built from

await sopops.post_session({"platform": "api", "external_id": call_id, "agent": "tonys-pizza", "release": ins.release,
                           "turns": [{"role": "user", "text": "Hi", "offset_s": 0.4}]})
```

| | |
|---|---|
| `SOPOPS_URL` | The workspace's API URL, e.g. `http://sopops.internal:8484/v1/workspaces/prod`. |
| `SOPOPS_TOKEN` | A workspace token (sopops Settings → Tokens) with the scopes the agent needs: `runtime` to fetch instructions, `ingest` to send sessions. `SOPOPS_INGEST_TOKEN` is read too. A workspace without a runtime token serves instructions without one. |
| `SOPOPS_CACHE_DIR` | Where the last good instructions are kept, default `~/.cache/sopops`. |

**Values only the call knows** (`{{placeholders}}` for the time, the caller, anything your code works out) are
filled in your process, so they never reach sopops:

```python
ins = await sopops.fetch_instructions("tonys-pizza", variables={"now": "7:15pm", "caller": "Sam"})
ins.unfilled                                         # placeholders nobody filled, e.g. ("special",)
```

A placeholder with no value is left as it is, and a value with no placeholder is ignored. Both log a warning and
neither stops the call. `attach` takes `variables` too.

Outside async code, one call does the same:

```python
import sopops

ins = sopops.fetch("tonys-pizza")          # the live release
ins = sopops.fetch("tonys-pizza", 14)      # pinned, e.g. one arm of an A/B test
prompt = ins.text
```

It reads the same environment variables, caches and falls back the same way, and builds a client per call, so inside
async code keep a `SopOps` and await `fetch_instructions` instead.

**A sopops outage never blocks a call.** The fetch gives up after `timeout` (2s by default). Then it returns the last good copy for that agent and release from disk (`ins.source == "cache"`), else your `fallback` (`"fallback"`), else raises `SopOpsUnavailable`. Sessions are retried on connection errors, 429 and 5xx; sending the same session again is safe.

## Streaming (optional)

```python
ins = await attach(ctx, session, agent="tonys-pizza", stream=True)
```

With `stream=True`, `attach` opens the session when the call starts and sends each turn and tool call as it happens (LiveKit's `conversation_item_added` and `function_tools_executed` events), in batches from a background task. It never blocks the agent and never raises into it: if sopops is slow or down, or the queue fills, events are logged and dropped. At shutdown it still posts the full session, which replaces what was streamed, so the default (`stream=False`, one post at the end) loses nothing. `on_guidance=` is accepted but not yet used: it's reserved for runtime guidance in a later version.

Outside LiveKit, use `SessionStream` directly:

```python
from sopops import SessionStream

stream = SessionStream(sopops, {"platform": "api", "external_id": call_id, "agent": "tonys-pizza", "release": ins.release})
stream.start()
stream.turn({"role": "user", "text": "Delivery please.", "at": time.time()})
stream.tool_call({"name": "check_delivery_zone", "arguments": {"address": "221 Mercer St"}, "result": {"in_zone": True}})
await stream.aclose()                                # sends what's queued (at most 1s), then stops
await sopops.post_session(full_session)
```

`SopOps.open_session` and `SopOps.append_events` are the two requests underneath (`POST …/sessions/open`, `POST …/sessions/<platform>/<id>/events`). Streaming needs the `ingest` scope, like posting.

The session format is in [API reference](../../docs/api/sessions.md#the-canonical-format). Adapters for other frameworks go in their own modules next to `sopops.livekit`, each importing its framework only when used. The TypeScript client is in [sdks/node](../node). A complete LiveKit agent is in [examples/livekit-restaurant](../../examples/livekit-restaurant).

## sopops-mcp (onboarding)

`sopops-mcp` is the MCP server a coding agent (Claude Code, Codex) runs to onboard existing agents into sopops. The sopops Import page gives you the command, with your server and a 2-hour onboarding token filled in:

```sh
claude mcp add sopops --env SOPOPS_URL=http://127.0.0.1:8484 --env SOPOPS_TOKEN=sopops_onb_... \
  -- uvx --from 'sopops[mcp] @ git+https://github.com/amanmibra/sopops@main#subdirectory=sdks/python' sopops-mcp
```

It reads ElevenLabs and Vapi agents locally (`ELEVENLABS_API_KEY`, `VAPI_API_KEY`, read-only) and writes the sopc project into the repo; everything else (the kickoff, the extraction guide, compiling, fidelity, pushing) goes to the sopops server's `/v1/onboarding` endpoints with `SOPOPS_URL` and `SOPOPS_TOKEN`. `sopops-mcp install-skill --url URL` copies the server's sopops-onboard skill into the repo (`--claude`, `--codex`).

## Development

```sh
cd sdks/python && uv run pytest
```
