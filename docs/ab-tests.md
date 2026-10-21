# A/B tests

Compare versions of an agent on your procedures: a different LLM, voice, speech model or release of your instructions. Results are under **Insights → A/B tests**.

## How sopops tells versions apart

Each session keeps what the agent ran with as its config: `llm`, `stt` and `tts` (each `<provider>/<model>`), `voice`, and any other setting you report. sopops hashes the config, and sessions with the same agent, release and config hash are one version. Each version is listed by an id hashed from those three.

The release is the prompt. sopops already knows which release each session ran, so you don't need to send your prompt. If you build the prompt outside sopops, report it as `prompt` (see [Prompts built outside sopops](#prompts-built-outside-sopops)).

A test name (`test`) is optional. Without one, the A/B tests page compares every version in the time range, across agents too. With one, it compares only the calls tagged with it.

## Split your traffic

sopops compares what each call reports, so your code decides which version a call gets. Hashing the call's id gives a stable 50/50 split:

```python
import hashlib

def pick(call_id: str, versions: list) -> dict:
    return versions[int(hashlib.sha256(call_id.encode()).hexdigest(), 16) % len(versions)]
```

## LiveKit with the SDK: nothing to add

`attach` reads the `AgentSession`'s LLM, speech-to-text and text-to-speech models, and the voice when the plugin keeps one, and sends them with the session.

```python
VERSIONS = [{"llm": "openai/gpt-4.1-mini"}, {"llm": "anthropic/claude-haiku-4-5"}]

async def entrypoint(ctx: JobContext):
    await ctx.connect()
    v = pick(ctx.job.id, VERSIONS)
    session = AgentSession(llm=v["llm"], stt="deepgram/nova-3", tts="cartesia/sonic-2")
    ins = await attach(ctx, session, agent="tonys-pizza", test="haiku-vs-gpt")
    await session.start(room=ctx.room, agent=Agent(instructions=ins.text))
```

- `test` names the test.
- `config` adds settings sopops can't read, like `{"turn_detection": "multilingual"}`, and wins over what it reads.
- `release=` (or `{"sopops_release": 3}` in job metadata) pins a release to compare instructions.
- In TypeScript, `recordSession(ctx, session, { sopops, test, config })` does the same.

## LiveKit without the SDK: one call

If sessions come from the no-code room capture, set `sopops_*` attributes on the agent right after `ctx.connect()`:

```ts
await ctx.room.localParticipant.setAttributes({
  sopops_test: "haiku-vs-gpt",
  sopops_llm: "anthropic/claude-haiku-4-5",
  sopops_tts: "cartesia/sonic-2", // optional: whatever differs between versions
});
```

In Python it's `await ctx.room.local_participant.set_attributes({...})`. Values are strings. `sopops_*` keys in job or room metadata count too.

## ElevenLabs

Add the API key under Settings → Platforms and set up the post-call webhook ([Connect](connect.md#connect-elevenlabs)). sopops reads each agent's LLM, temperature, TTS model, voice and speech-to-text provider, with a call's own overrides on top. Running two ElevenLabs agents with different settings needs nothing else.

To name a test or add settings, send them as dynamic variables: `sopops_test`, `sopops_version`, and so on. The TypeScript SDK does this with `elevenLabsSession(sopops, { agent, test, config })`.

## Vapi

With the end-of-call report on and the API key under Settings → Platforms, sopops reads the assistant's model, voice and transcriber, with the call's `assistantOverrides` on top. Put `sopops_test` and other settings in the call's or assistant's `metadata`.

## Prompts built outside sopops

Send the template as `prompt` in `config`, before caller details are filled in. A prompt with the caller's name or order in it is different on every call, so every call would be its own version. sopops stores each prompt once and keys the config on its hash.

## Any other platform

Add `test` and `config` to the session you post (`POST /v1/workspaces/{ws}/sessions`):

```json
{"platform": "api", "external_id": "call_123", "agent": "front-desk", "test": "haiku-vs-gpt",
 "config": {"llm": "anthropic/claude-haiku-4-5", "voice": "ava"}, "turns": []}
```

`sopops_*` keys in `metadata` count the same way and win over `config`.

## Read the results

Insights → A/B tests works in two steps:

1. **Pick versions.** Each version is one row: its id, agent, release and settings. Tick up to five and press **Compare**. They're lettered A to E in the order you tick them, and A is the baseline, the version the others are measured against.
2. **Compare.** What differs between the versions, a chart of each procedure's pass rate with its 95% range, then the procedure and rule tables. Hover a letter to see its version id. **Make baseline** changes A, and **Change versions** goes back to step 1.

- **Procedures:** pass rate, how often the goal was met, and how many calls each procedure applied to.
- **Rules:** pass rate per step and never rule. Warning signs show how often they came up, without a verdict, since the caller brings them.
- **Better or worse:** a version is called better or worse than the baseline only with 10 or more results on each, a change of at least 5 points, and 95% ranges that don't overlap. Otherwise it says the change could be chance, or that there are too few results.

Rates count only checked calls. Calls still being checked count toward a version's sessions, not its rates.

## Limits

- Up to 32 settings per call.
- Keys up to 64 characters, values up to 1,024, and a prompt up to 65,536.
- A setting over a limit is left out with a warning on the ingest response. The session is still stored.
