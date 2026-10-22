# A/B tests

Compare versions of an agent on your procedures: a different LLM, voice, speech model or release of your instructions. Results are under **Insights → A/B tests**.

## How sopops tells versions apart

A version is one agent, one release and one config hash, listed by an id hashed from those three. Each session reports its config: `llm`, `stt` and `tts` (each `<provider>/<model>`), `voice`, and any other setting you send.

- **Release.** sopops already knows which release each session ran, so you don't send your prompt. If you build it outside sopops, see [Prompts built outside sopops](#prompts-built-outside-sopops).
- **Test name.** `test` is optional. Without one, the page compares every version in the time range, across agents too. With one, only the calls tagged with it.

## Split your traffic

Your code decides which version a call gets. Hashing the call's id gives a stable split, so the same call always lands on the same side:

```python
import hashlib

def pick(call_id: str, versions: list) -> dict:
    return versions[int(hashlib.sha256(call_id.encode()).hexdigest(), 16) % len(versions)]
```

```ts
import { createHash } from "node:crypto";

function pick<T>(callId: string, versions: T[]): T {
  const digest = BigInt(`0x${createHash("sha256").update(callId).digest("hex")}`);
  return versions[Number(digest % BigInt(versions.length))];
}
```

What goes in a version is yours to decide. Three shapes teams use:

### Two configs, 50/50

A whole config per side. Anything you can write as settings can differ: models, turn detection, a feature flag, a nested section of your own.

```python
VERSIONS = [
    {"turn_detection": "server_vad", "pipeline": {"interrupt_min_words": 2, "endpoint_ms": 500}},
    {"turn_detection": "multilingual", "pipeline": {"interrupt_min_words": 0, "endpoint_ms": 320}},
]

v = pick(ctx.job.id, VERSIONS)
ins = await attach(ctx, session, agent="front-desk", test="turn-detection", config=v)
```

```ts
const VERSIONS = [
  { turn_detection: "server_vad", pipeline: { interrupt_min_words: 2, endpoint_ms: 500 } },
  { turn_detection: "multilingual", pipeline: { interrupt_min_words: 0, endpoint_ms: 320 } },
];

const v = pick(ctx.job.id, VERSIONS);
const ins = await attach(ctx, session, { agent: "front-desk", test: "turn-detection", config: v });
```

### One model against another

`attach` reads the session's LLM, speech-to-text and text-to-speech models, and the voice when the plugin keeps one, so a model test needs no `config` at all.

```python
VERSIONS = [{"llm": "openai/gpt-4.1-mini"}, {"llm": "anthropic/claude-haiku-4-5"}]

v = pick(ctx.job.id, VERSIONS)
session = AgentSession(llm=v["llm"], stt="deepgram/nova-3", tts="cartesia/sonic-2")
ins = await attach(ctx, session, agent="front-desk", test="haiku-vs-gpt")
```

```ts
const VERSIONS = [{ llm: "openai/gpt-4.1-mini" }, { llm: "anthropic/claude-haiku-4-5" }];

const v = pick(ctx.job.id, VERSIONS);
const session = new voice.AgentSession({ llm: v.llm, stt: "deepgram/nova-3", tts: "cartesia/sonic-2" });
const ins = await attach(ctx, session, { agent: "front-desk", test: "haiku-vs-gpt" });
```

### A release and a model at once

Two releases by two models is four versions. sopops keys a version on both, so the table separates them and a change in your instructions doesn't get read as a change in the model.

```python
VERSIONS = [{"release": r, "llm": m}
            for r in (7, 8)
            for m in ("openai/gpt-4.1-mini", "anthropic/claude-haiku-4-5")]

v = pick(ctx.job.id, VERSIONS)
session = AgentSession(llm=v["llm"], stt="deepgram/nova-3", tts="cartesia/sonic-2")
ins = await attach(ctx, session, agent="front-desk", release=v["release"], test="rewrite-x-model")
```

```ts
const VERSIONS = [7, 8].flatMap(release =>
  ["openai/gpt-4.1-mini", "anthropic/claude-haiku-4-5"].map(llm => ({ release, llm })));

const v = pick(ctx.job.id, VERSIONS);
const session = new voice.AgentSession({ llm: v.llm, stt: "deepgram/nova-3", tts: "cartesia/sonic-2" });
const ins = await attach(ctx, session, { agent: "front-desk", release: v.release, test: "rewrite-x-model" });
```

Four arms take roughly four times the calls to separate. With low volume, run two at a time.

### What you can pass

| Argument | What it does |
|---|---|
| `test` | Names the test. Without one, the page compares every version in the time range. |
| `config` | Settings sopops can't read for itself. A value can be a scalar or a whole object or array, and it wins over what sopops reads. |
| `release` | Pins a release. `{"sopops_release": 3}` in job or room metadata does the same. |

`record_session` (`recordSession` in TypeScript) takes the same `test` and `config`.

## LiveKit without the SDK: one call

For sessions from the no-code room capture, set `sopops_*` attributes on the agent right after `ctx.connect()`:

```python
await ctx.room.local_participant.set_attributes({
    "sopops_test": "haiku-vs-gpt",
    "sopops_llm": "anthropic/claude-haiku-4-5",
    "sopops_tts": "cartesia/sonic-2",  # optional: whatever differs between versions
})
```

```ts
await ctx.room.localParticipant.setAttributes({
  sopops_test: "haiku-vs-gpt",
  sopops_llm: "anthropic/claude-haiku-4-5",
  sopops_tts: "cartesia/sonic-2", // optional: whatever differs between versions
});
```

Values are strings. `sopops_*` keys in job or room metadata count too.

## ElevenLabs

Add the API key under Settings → Platforms and set up the post-call webhook ([Connect](connect.md#connect-elevenlabs)). sopops reads each agent's LLM, temperature, TTS model, voice and speech-to-text provider, with a call's overrides on top. Two agents with different settings need nothing else.

Send a test name or extra settings as dynamic variables: `sopops_test`, `sopops_version`, and so on. The TypeScript SDK does this with `elevenLabsSession(sopops, { agent, test, config })`.

## Vapi

With the end-of-call report on and the API key under Settings → Platforms, sopops reads the assistant's model, voice and transcriber, with the call's `assistantOverrides` on top. Put `sopops_test` and other settings in the call's or assistant's `metadata`.

## Prompts built outside sopops

Send the template as `prompt` in `config`, before caller details are filled in. A prompt carrying a caller's name or order differs on every call, so every call would be its own version. sopops stores each prompt once and keys the config on its hash.

## Any other platform

Add `test` and `config` to the session you post (`POST /v1/workspaces/{ws}/sessions`):

```json
{"platform": "api", "external_id": "call_123", "agent": "front-desk", "test": "haiku-vs-gpt",
 "config": {"llm": "anthropic/claude-haiku-4-5", "voice": "ava"}, "turns": []}
```

`sopops_*` keys in `metadata` count the same way and win over `config`.

## Read the results

1. **Pick versions.** Tick up to five rows (id, agent, release, settings) and press **Compare**. They are lettered A to E in tick order, and A is the baseline the others are measured against.
2. **Compare.** What differs, a chart of each procedure's pass rate with its 95% range, then the tables below. Hover a letter for its version id. **Make baseline** changes A; **Change versions** goes back.

| Table | Shows |
|---|---|
| Procedures | Pass rate, how often the goal was met, and how many calls each procedure applied to. |
| Rules | Pass rate per step and never rule. Warning signs show how often they came up, without a verdict, since the caller brings them. |

- **Better or worse** needs 10 or more results on each side, a change of at least 5 points, and 95% ranges that don't overlap. Otherwise it says the change could be chance, or that there are too few results.
- Rates count only checked calls. Calls still being checked count toward a version's sessions, not its rates.

## Limits

- Up to 32 settings per call, keys up to 64 characters.
- A string value up to 1,024 characters, a prompt up to 65,536.
- An object or array up to 8,192 characters of JSON, nested up to 8 levels.
- A setting over a limit is left out with a warning on the ingest response. The session is still stored.
