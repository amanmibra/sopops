# SDK reference

Most agents reach sopops through one of the two clients: `sopops` on PyPI and `sopops` on npm. Both do the same two things, fetch an agent's instructions when a call starts and send the finished session back, and both name things the same way, snake_case in Python and camelCase in TypeScript. [Connect your agent](connect.md) wires one up end to end; this page is the surface it uses.

## What each import path holds

| Python | TypeScript | Holds |
|---|---|---|
| `sopops` | `sopops` | `SopOps`, `Instructions`, `SessionStream`, the two errors, and the helpers for placeholders and pinned releases. |
| `sopops.livekit` | `sopops/livekit` | The LiveKit Agents adapter: `attach`, `record_session`, `from_livekit`, `pinned_release`, `agent_config`. |
| | `sopops/elevenlabs` | `elevenLabsSession`, for browser calls on `@elevenlabs/client` or `@elevenlabs/react`. Python has no ElevenLabs adapter. |
| | `sopops/node` | `FileCache`, the last good instructions on disk. Python caches on disk already. |

Python needs 3.10 or later and httpx, and the LiveKit adapter is the `livekit` extra. The TypeScript package is ESM, has no dependencies, and runs on Node 18+, in browsers and on edge runtimes. Neither adapter imports its framework at import time, so importing one costs nothing in a process that doesn't use it.

## Settings come from the environment unless you pass them

| Variable | Read by | Value |
|---|---|---|
| `SOPOPS_URL` | the `SopOps` constructor | The workspace's API URL, `https://app.sopops.dev/v1/workspaces/<id>`. With no `url` and no variable, the constructor raises. |
| `SOPOPS_TOKEN` | the `SopOps` constructor | A workspace token. `SOPOPS_INGEST_TOKEN` is read when it isn't set. |
| `SOPOPS_CACHE_DIR` | Python's `SopOps`, TypeScript's `FileCache` | Where the last good instructions are kept. Default `~/.cache/sopops`. |

A token's scope decides what it can do: `runtime` fetches instructions, `ingest` sends sessions. Anything that does both, like the LiveKit adapter's `attach`, needs both scopes on the one token. TypeScript reads the environment only where `process.env` exists, so a browser passes `url` and `token` itself.

Python logs through the `sopops`, `sopops.stream` and `sopops.livekit` loggers; TypeScript writes the same lines to the console.

## `SopOps` holds the connection

```python
from sopops import SopOps

sopops = SopOps()                                       # or SopOps(url, token, cache_dir="/var/cache/sopops")
```

```ts
import { SopOps } from "sopops";
import { FileCache } from "sopops/node";

const sopops = new SopOps({ cache: new FileCache() });  // Node: keep the copy on disk
```

| Python | TypeScript | Default | What it is |
|---|---|---|---|
| `url` | `url` | `SOPOPS_URL` | The workspace's API URL. A trailing slash is trimmed. |
| `token` | `token` | `SOPOPS_TOKEN`, else `SOPOPS_INGEST_TOKEN` | Sent as `Authorization: Bearer`. Left off entirely when there is none, which a workspace with no token yet still answers. |
| `timeout` | `timeoutMs` | 2.0 seconds, 2000 ms | How long the call-start fetch may take before the cached copy is used. The per-request defaults below override it elsewhere. |
| `cache_dir` | `cache` | `SOPOPS_CACHE_DIR` or `~/.cache/sopops`, and in TypeScript a `MemoryCache` shared by the process | Python writes JSON files; TypeScript keeps instructions in memory until you hand it a cache. |
| `transport` | `fetch` | httpx's default, `globalThis.fetch` | For tests, and for a runtime with its own fetch. |

In Python `url` and `token` are positional and the rest keyword-only; in TypeScript it is all one options object. Python opens a fresh httpx client per request, so keeping one `SopOps` for the process is free.

## Fetching instructions falls back before it fails

```python
ins = await sopops.fetch_instructions("tonys-pizza", fallback=FALLBACK)
```

```ts
const ins = await sopops.fetchInstructions("tonys-pizza", { fallback: FALLBACK });
```

`fetch_instructions(agent, release=None, *, fallback=None, variables=None)` and `fetchInstructions(agent, options?)` both answer with an `Instructions`. TypeScript takes everything after `agent` as a `FetchOptions` object.

| Argument | What it does |
|---|---|
| `agent` | The sopc agent id (`tonys-pizza`), its platform ref (`livekit:tonys-pizza`), or the platform's own id on its own. |
| `release` | Pins one release, for an arm of an [A/B test](ab-tests.md). Without it you get the live release. |
| `fallback` | Text to serve when sopops is down and nothing is cached. |
| `variables` | Values for the <code v-pre>{{placeholders}}</code> only this call knows. See below. |

The fetch never waits longer than the client's timeout, and never raises because sopops is unwell:

| What you get | `source` | When |
|---|---|---|
| The live release | `live` | sopops answered in time. |
| The last good copy | `cache` | sopops timed out, refused, or answered an error, and this url, agent and release were fetched before. |
| The `fallback` text | `fallback` | Nothing cached. `release` comes back `None`. |
| `SopOpsUnavailable` | | Nothing cached and no `fallback`. |

Every live fetch writes the cache, keyed by the workspace URL, the agent and the release, before any variables are filled, so a cached copy still fills with the next call's own values. Python writes it atomically under `cache_dir`, and a cache it can't write only logs. Falling back to the cache logs a warning, falling back to `fallback` logs an error.

## `Instructions` is what you got and where it came from

A frozen dataclass in Python, an interface in TypeScript.

| Python | TypeScript | Type | What it is |
|---|---|---|---|
| `text` | `text` | string | The compiled prompt, with whatever variables you passed already filled. |
| `release` | `release` | int or null | The release it came from. Null for a fallback. |
| `agent` | `agent` | string | The sopc agent id, as sopops resolved it. Pass this one back when you post the session. |
| `blocks` | `blocks` | list | Each file the prompt was built from, in prompt order: `kind`, `id`, `path`, `version`. |
| `hash` | `hash` | string or null | The compiled prompt's hash. |
| `platform_ref` | `platformRef` | string or null | `livekit:tonys-pizza`, `elevenlabs:<agent id>`, `vapi:<assistant id>`. |
| `tools` | `tools` | list of strings | The tools the agent's procedures call, by name. |
| `tool_sops` | `toolSops` | list of strings | Procedures delivered on demand rather than written into the prompt: their ids, fetched with the `get_sop` tool ([Releases](api/releases.md)). |
| `source` | `source` | `live`, `cache` or `fallback` | Where the text came from. Worth logging. |
| `unfilled` | `unfilled` | tuple, array | Placeholders still in `text`. Empty unless you passed `variables`. |

## Variables are filled in your process, so they never reach sopops

The time, the caller, a row from your own database: sopops can't know them, so it serves the <code v-pre>{{placeholder}}</code> as it is and the SDK substitutes it where the call runs. A placeholder you pass no value for stays a placeholder and is listed in `ins.unfilled`, and a name that matches nothing in the prompt logs a warning. Neither stops the call.

```python
ins = await sopops.fetch_instructions("tonys-pizza", variables={"now": "7:15pm", "caller": "Sam"})
if ins.unfilled:
    log.warning("no value for %s", ins.unfilled)
```

```ts
const ins = await sopops.fetchInstructions("tonys-pizza", { variables: { now: "7:15pm", caller: "Sam" } });
if (ins.unfilled?.length) console.warn(`no value for ${ins.unfilled}`);
```

The substitution itself is two functions: `fill(text, variables)` returns the text with every known placeholder replaced, and `unfilled(text)` returns the ones left, in order and without repeats. TypeScript exports both from `sopops`; in Python they live in `sopops.client` rather than the package root.

## Sending a finished session

```python
out = await sopops.post_session(session, retries=2, timeout=5.0)
```

```ts
const out = await sopops.postSession(session, { retries: 2, timeoutMs: 5000 });
```

`session` is one conversation in sopops's [canonical format](api/sessions.md#the-canonical-format), typed as `SessionIn` in TypeScript. Posting is safe to repeat: sopops upserts on `platform` and `external_id`. Connection errors, 429 and 5xx are retried with a widening wait, `retries` times; anything else in the 4xx range raises `SopOpsError` straight away. The answer is `{id, created, updated, status, warnings}`, a `SessionIngested` in TypeScript.

## Streaming a session while the call runs

A stream is the same session arriving in pieces. It exists so a long call shows up in sopops before it ends; the full session posted at the end still replaces everything streamed, so the stream is never the record of truth.

```python
from sopops import SessionStream

stream = SessionStream(sopops, {"platform": "api", "external_id": call_id, "agent": ins.agent, "release": ins.release})
stream.start()                                   # from inside the running event loop
stream.turn({"role": "user", "text": "Delivery please.", "at": time.time()})
stream.tool_call({"name": "check_delivery_zone", "arguments": {...}, "result": {...}})
await stream.aclose()                            # sends what's queued, then stops
```

```ts
const stream = sopops.stream({ platform: "api", external_id: callId, agent: ins.agent, release: ins.release });
stream.turn({ role: "user", text: "Delivery please.", at: new Date().toISOString() });
stream.toolCall({ name: "check_delivery_zone", arguments: {}, result: {} });
await stream.close();
```

| Python | TypeScript | What it does |
|---|---|---|
| `SessionStream(client, session, ...)` | `sopops.stream(session, options?)` | Builds the stream. `session` holds the fields `open_session` takes; `platform` and `external_id` are required. |
| `start()` | | Starts the background sender, from inside the running loop. TypeScript sends on a timer and needs no start. |
| `turn(turn)` | `turn(turn)` | Queues a turn: `role`, `text`, and `at` or `offset_s`. |
| `tool_call(call)` | `toolCall(call)` | Queues a tool call: `name`, `arguments`, `result`, `is_error`, `call_id`, and `at` or `offset_s`. |
| `aclose(timeout=1.0)` | `close(timeoutMs = 1000)` | Sends what's queued, then stops, giving up after the timeout. Never raises. |
| `sent`, `dropped` | `sent`, `dropped` | How many events got through, and how many didn't. |

| Setting | Python | TypeScript | Default |
|---|---|---|---|
| How often a batch goes out | `flush_interval` | `flushMs` | 0.5 seconds, 500 ms |
| Events per request | `max_batch` | `maxBatch` | 50 |
| Events waiting to be sent | `max_queue` | `maxQueue` | 1000 |
| Runtime guidance back from sopops | `on_guidance` | `onGuidance` | Not yet used, reserved for a later version |

Queueing an event never blocks and never raises. Past `max_queue` events are counted in `dropped` and thrown away, and a batch sopops refuses is logged and thrown away too, because an agent mid-call is the wrong place to handle either.

Underneath, the stream calls two methods you can also call yourself: `open_session(session)` and `openSession(session)`, which answer `{id, created}` and are keyed the same way `post_session` is, and `append_events(platform, external_id, events)` and `appendEvents(platform, externalId, events)`, which answer `{accepted, duplicates}`. An event is `{seq, type, turn}` or `{seq, type, tool_call}` with `seq` counting from 0 per session; order doesn't matter and a `seq` sent twice is ignored. Both default to a 3 second timeout and neither retries.

## Linking a platform's call id to the release it ran on

When you fetch instructions yourself and the platform posts the session later, sopops needs the two tied together, or the session arrives with no release on it.

```python
await sopops.record_served("elevenlabs", conversation_id, ins.agent, ins.release)
```

```ts
await sopops.recordServed({ platform: "elevenlabs", callId: conversationId, agent: ins.agent, release: ins.release });
```

Call it as soon as the platform's own id exists: an ElevenLabs conversation id, a Vapi call id. It answers nothing and raises `SopOpsError` on an error status, with a 3 second timeout by default. The ElevenLabs adapter does this for you.

## One call, with no client to keep

```python
from sopops import fetch

ins = fetch("tonys-pizza", 14, fallback=FALLBACK)
```

```ts
import { fetchInstructions } from "sopops";

const ins = await fetchInstructions("tonys-pizza", 14, { fallback: FALLBACK });
```

Python's `fetch(agent, release=None, *, fallback, variables, url, token, timeout=2.0, cache_dir)` blocks, for code that isn't async, and raises `RuntimeError` if you call it inside a running event loop. TypeScript's `fetchInstructions(agent, release?, options)` takes the client's own options plus `fallback`, and has no `variables`. Both build a client per call and fall back exactly as the method does, so anything hot should keep a `SopOps` instead.

## Reading a release pinned in metadata

`release_pin(metadata)` and `releasePin(metadata)` pull a pinned release out of job or room metadata, written either as `{"sopops_release": 3}` or as `{"sopops": {"release": 3}}`, passed as a JSON string or an already parsed object. A boolean, or anything that isn't a number, comes back as none. The LiveKit adapter reads the pin for you; see [A/B tests](ab-tests.md) for what pinning is for.

## Where TypeScript keeps the last good copy

Python caches on disk and needs nothing. TypeScript takes a cache on the client, and anything with these two methods will do:

```ts
interface InstructionCache {
  get(key: string): Promise<Instructions | undefined> | Instructions | undefined;
  set(key: string, value: Instructions): Promise<void> | void;
}
```

| Class | Where | Survives |
|---|---|---|
| `MemoryCache` from `sopops` | A `Map` in the process. The default, shared by every client that takes no cache. | The process. |
| `FileCache` from `sopops/node` | One JSON file per key under `dir`, default `SOPOPS_CACHE_DIR` or `~/.cache/sopops`. Written to a temporary name and renamed, so a crash never leaves half a file. | A restart. |

## The LiveKit adapter does both halves

```python
from sopops.livekit import attach

ins = await attach(ctx, session, agent="livekit:tonys-pizza", fallback=FALLBACK, stream=True)
```

```ts
import { attach } from "sopops/livekit";

const ins = await attach(ctx, session, { agent: "livekit:tonys-pizza", fallback: FALLBACK });
```

`attach` fetches the instructions, notes the release in `session.userdata`, and registers the shutdown callback that posts the session. Call it after `ctx.connect()`, or the room's metadata isn't there to read a pin from.

| Python | TypeScript | What it does |
|---|---|---|
| `client` | `sopops` | The client to use. Default: a new `SopOps()`. |
| `agent` | `agent` | The sopc agent id or platform ref. Default: `livekit:` plus the job's agent name. |
| `release` | `release` | Pins a release. Default: a pin in the job's or the room's metadata, else the live release. |
| `fallback` | `fallback` | Text to use when sopops is down and nothing is cached. |
| `variables` | `variables` | Values for the placeholders only this call knows. |
| `test` | `test` | Names the [A/B test](ab-tests.md) this call is part of. |
| `config` | `config` | Settings to report beyond the models read off the session. Yours win. |
| `stream` | | Python only: also send each turn and tool call as the call runs. |
| `on_guidance` | | Not yet used, reserved for a later version. |

In Python `agent` is the third positional argument and the rest are keyword-only; in TypeScript they are one options object, and the client is named `sopops` there rather than `client`.

| Function | What it does |
|---|---|
| `record_session(ctx, session, *, client, agent, stream, test, config)`, `recordSession(ctx, session, options)` | The posting half on its own, for code that got its instructions some other way. It posts `session.history` at shutdown, keyed by the job id because rooms get reused, and logs its errors rather than raising them into shutdown. Python reads the release from `session.userdata`; TypeScript takes `release` in its options, and its `sopops` client is required. |
| `from_livekit(history)`, `fromLiveKit(history)` | Maps a LiveKit `ChatContext`, or its dict form, to sopops turns and tool calls. Python answers a `(turns, tool_calls)` tuple, TypeScript a `{turns, toolCalls}` object. A message repeating the one before it is dropped, because LiveKit can add a user transcript twice. |
| `pinned_release(ctx)`, `pinnedRelease(ctx)` | The release pinned in the job's dispatch metadata, else the room's. |
| `agent_config(session)`, `agentConfig(session)` | What the session runs with: `llm`, `stt` and `tts` as `<provider>/<model>`, plus `voice` when the TTS names one. Whatever a plugin doesn't report is left out, which is what makes release comparisons work with no extra setup. |

## The ElevenLabs adapter is TypeScript only

`elevenLabsSession(sopops, input)` fetches the instructions and builds the `startSession` options that carry them, so a browser call runs the live release. The agent has to allow overriding its system prompt under Security → Overrides, or ElevenLabs refuses the session.

```ts
import { elevenLabsSession } from "sopops/elevenlabs";

const s = await elevenLabsSession(sopops, { agent: "front-desk", stream: true });
const conversation = await Conversation.startSession({ ...s.sessionOptions });
```

| Input | What it does |
|---|---|
| `agent` | The sopc agent id or platform ref. Default: `elevenlabs:` plus `agentId`. |
| `agentId` | The ElevenLabs agent id. Default: the one in the sopops agent's platform ref. One of the two has to be there. |
| `stream` | Send messages and tool results as they happen, and post the whole conversation when it ends. Needs `ingest`. Default false. |
| `test`, `config` | Sent as the dynamic variables `sopops_test` and `sopops_<key>`. sopops reads the agent's LLM, voice and TTS model itself. |
| `release`, `fallback`, `variables` | As on `fetchInstructions`. |

What comes back carries `instructions`, the `sessionOptions` to spread into `startSession`, `link(conversationId)` and `end(reason?)`. The built-in `onConnect` calls `link` for you, which is what ties the post-call session to the release it ran on; pass your own `onConnect` and you have to call `link` from it. With `stream: true`, `onDisconnect` calls `end`, which closes the stream and posts the full conversation. Neither `link` nor `end` ever throws, and calling `end` twice returns the same promise.

## Errors

| Error | When |
|---|---|
| `SopOpsError` | sopops answered with an error status, refused a session, or couldn't be reached. TypeScript carries the HTTP status on `.status`, null when the request never landed. |
| `SopOpsUnavailable` | A subclass of the above: no instructions at all, because sopops failed, nothing was cached, and no `fallback` was given. |

Fetching, posting and `record_served` raise these. Streaming never does, and neither does the LiveKit adapter's shutdown post.
