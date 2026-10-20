---
name: sopops-ab-tests
description: Set up an A/B test of a voice agent's settings (LLM, voice, speech models, prompt release) so sopops compares the versions on the team's procedures. Finds where the agent's settings live in a LiveKit, ElevenLabs or Vapi project, adds a stable traffic split and the sopops tags, and explains where to read results. Use when the user asks to A/B test, compare models or voices, or try a new prompt on part of their calls.
---

# Set up an A/B test for sopops

sopops compares versions of an agent on the team's procedures (Insights → A/B tests). A version is the calls with the same agent, release and settings. Your job is to make the agent run two or more versions and make each call report which one it ran. You change code in this repo only; the user deploys.

Full reference: https://github.com/amanmibra/sopops/blob/main/docs/ab-tests.md

## 1. Find how the agent is built

- **LiveKit (Python):** the entrypoint that builds `AgentSession(llm=..., stt=..., tts=...)`. Check whether it calls `attach` from `sopops.livekit`.
- **LiveKit (Node):** `new voice.AgentSession({...})`, and whether it calls `recordSession` from `sopops/livekit`.
- **ElevenLabs:** the agent's settings live on ElevenLabs. Look for `elevenLabsSession` from `sopops/elevenlabs`, `Conversation.startSession` overrides, or a conversation-initiation webhook.
- **Vapi:** an assistant id, or an `assistant-request` server that returns `assistantOverrides`.

Note where the settings that would change are defined, and how sessions reach sopops (the SDK, or the no-code capture in sopops Settings).

## 2. Agree on the test

Ask the user, unless they already said:

- **What changes:** one thing per test if possible, like the LLM, the voice, or the release of their instructions.
- **The split:** 50/50 unless they say otherwise.
- **A test name:** short and lowercase, like `haiku-vs-gpt`.

## 3. Add the split

Pick a version from the call's id, so a call keeps its version through retries and the split is stable. Keep every version's settings in one place:

```python
import hashlib

VERSIONS = [{"llm": "openai/gpt-4.1-mini"}, {"llm": "anthropic/claude-haiku-4-5"}]

def pick(call_id: str) -> dict:
    return VERSIONS[int(hashlib.sha256(call_id.encode()).hexdigest(), 16) % len(VERSIONS)]
```

Use the LiveKit job id (`ctx.job.id`), the ElevenLabs conversation or caller id, or the Vapi call id.

## 4. Make each call report its version

- **LiveKit with `attach` (or `recordSession`):** build the `AgentSession` from the picked version and pass `test="<name>"`. sopops reads the session's models itself. Pass `config={...}` only for settings it can't read, like turn detection.
- **LiveKit without the SDK:** right after `ctx.connect()`, set the agent's attributes: `sopops_test` and one `sopops_<setting>` per setting that differs (`sopops_llm`, `sopops_tts`, `sopops_voice`). Values are strings.
- **ElevenLabs:** with the sopops API key in Settings, sopops reads each agent's LLM, voice and TTS model, plus per-call overrides. So the simplest test is two ElevenLabs agents and a split between them, with no code for the tags. With `elevenLabsSession`, pass `test` and `config`, and put the voice in the session overrides (the agent must allow that override).
- **Vapi:** with the sopops API key in Settings, sopops reads the assistant's model, voice and transcriber, plus the call's `assistantOverrides`. Return the picked version's `model` or `voice` in `assistantOverrides`, and put `sopops_test` in its `metadata`.
- **Instructions (prompts):** compare releases with a release pin (`release=` on `attach`, or `{"sopops_release": N}` in job metadata). A prompt built outside sopops goes in `config` as `prompt`, as the template before caller details are filled in.

## 5. Check and hand back

- Run the project's tests or type checks.
- Tell the user:
  - what changed and where;
  - the split;
  - the test name;
  - that results show under Insights → A/B tests once calls are checked;
  - that each version needs at least 10 checked calls before sopops calls one better or worse.

Never deploy, change platform settings (ElevenLabs agents, Vapi assistants, LiveKit projects), or send live traffic yourself. Never print or copy keys or tokens.
