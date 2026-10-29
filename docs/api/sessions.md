# Sessions

A session is one conversation an agent had: a voice call or a text chat. Sessions come from LiveKit (the SDK adapters, [Python](../../sdks/python) and [Node](../../sdks/node), or no-code room capture), ElevenLabs or Vapi, or from OpenTelemetry, a file import or the API. All of them use one [canonical format](#the-canonical-format).

## Send and read sessions

| Endpoint | What it does |
|---|---|
| `POST /v1/workspaces/{ws}/sessions` | Ingest one session, with `Authorization: Bearer <token>` (scope `ingest`). An upsert on `(platform, external_id)`. |
| `GET /v1/workspaces/{ws}/sessions` | Newest first by start time: `{items, next_cursor}`. Each item has `status` and `result` (the worst procedure result, once checked). |
| `GET /v1/workspaces/{ws}/sessions/{id}` | One session with `turns`, `tool_calls`, `metadata`, `checks` ([SOP checks](checks-and-review.md#sop-checks)), `model_calls` and `cost_usd`. |
| `GET /v1/workspaces/{ws}/sessions/stream` | Server-sent events: `event: session` with `data: {type: created \| updated, workspace, id, status}` each time a session is ingested or processed. A comment every 15s keeps it open. |

Ingest needs a token with the `ingest` scope ([Tokens](teams-and-access.md#tokens)). The rest follows the usual [access rules](teams-and-access.md#sign-in-and-access).

```sh
curl -s -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/sessions" \
  -d @session.json
```

```json
{
  "id": "6e549e2c-9d94-4df9-a92f-3f1a5da91624",
  "created": true,
  "updated": false,
  "status": "received",
  "warnings": [
    "Release 99 doesn't exist in workspace 'harbor-qa'; the session is kept without it.",
    "Config 'notes' is too long (values up to 1,024 characters); it was left out."
  ]
}
```

| Response | When |
|---|---|
| `201 {id, created: true, updated: false, status, warnings}` | A new session. |
| `200` with `updated: true` | Its content changed. |
| `200` with `created` and `updated` both false | An identical re-send, which changes nothing. |
| `401` | No valid token for the workspace. |
| `403` | A token without the `ingest` scope. |

A new or changed session runs `process_session`.

### List filters

| Filter | Type | |
|---|---|---|
| `agent` | string | The sop id, or the agent as sent. |
| `channel`, `status`, `result`, `platform`, `release` | enum, int | Exact match. |
| `since`, `until` | ISO 8601 | On `started_at`. |
| `cursor` | string | The previous page's `next_cursor`. |
| `limit` | int | Default 50, at most 200. |

Drill-down filters, the ones [Insights](insights.md) links to:

| Filter | Type | |
|---|---|---|
| `procedure` | string | The router considered it. Adds `procedure_check` to each item: that procedure's result, `goal_reached`, and one dot per rule. |
| `procedure_result` | enum | That procedure's result. |
| `rule` | string | Any wording of the rule (its [lineage](checks-and-review.md#rule-lineage)), with optional `rule_status` (repeatable). |
| `pattern` | string | A rule pattern includes its sub-patterns. |
| `theme` | string | The theme its patterns belong to. |
| `finding` | bool | true: with any finding; false: with none. |
| `eligible` | bool | The sessions Insights rates: checked or incomplete, with a result. |
| `suppressed` | bool | false: the finding filters leave suppressed findings out, as Insights does; true: only them. |

```json
{
  "items": [
    {
      "id": "689038cd-9c8c-4c8b-abfe-a290cafa1772",
      "workspace": "harbor-qa",
      "agent_id": "copper-wok-harbor",
      "agent_ref": "copper-wok-harbor",
      "channel": "voice",
      "platform": "livekit",
      "external_id": "ab-mock-832e3f1125",
      "release_id": null,
      "version_ref": null,
      "test": "haiku-vs-gpt",
      "config": {"llm": "anthropic/claude-haiku-4-5", "stt": "deepgram/nova-3", "tts": "cartesia/sonic-2", "voice": "ava"},
      "config_hash": "a157e7efe416d456",
      "started_at": "2026-10-20T21:18:18.519219Z",
      "ended_at": "2026-10-20T21:21:18.519219Z",
      "duration_s": 180.0,
      "status": "checked",
      "status_reason": null,
      "result": "followed",
      "turn_count": 9,
      "tool_call_count": 2,
      "created_at": "2026-10-20T22:08:18.857212Z",
      "updated_at": "2026-10-20T22:09:47.428039Z",
      "processed_at": "2026-10-20T22:09:47.428039Z",
      "procedure_check": null
    }
  ],
  "next_cursor": "WyIyMDI2LTEwLTIwVDIxOjE4OjE4LjUxOTIxOSswMDowMCIsICI2ODkwMzhjZC05YzhjLTRjOGItYWJmZS1hMjkwY2FmYTE3NzIiXQ"
}
```

### Streaming (optional)

A sender can also stream a session as it happens, then close it. Nothing is checked until it closes, so a streamed session ends up the same as one posted at the end.

| Endpoint | What it does |
|---|---|
| `POST /v1/workspaces/{ws}/sessions/open` | `{platform, external_id, channel?, agent?, release?, version_ref?, started_at?, metadata?, test?, config?}`. Creates the session with status `open` (`201`), or returns the existing one (`200`). |
| `POST /v1/workspaces/{ws}/sessions/{platform}/{external_id}/events` | `{events: [{seq, type: turn \| tool_call, turn? \| tool_call?}]}`, up to 500. Returns `{accepted, duplicates}`. `404` if the session was never opened. |
| `POST /v1/workspaces/{ws}/sessions/{platform}/{external_id}/close` | `{ended_at?, partial?, test?, config?}`. `config` is merged over the one sent at open, key by key, and a nested setting is replaced whole. Closes the session with what streamed in and runs `process_session`. |

```sh
curl -s -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/sessions/open" \
  -d '{"platform": "livekit", "external_id": "AJ_tp_8f2k1", "agent": "copper-wok-harbor", "release": 4}'

curl -s -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/sessions/livekit/AJ_tp_8f2k1/events" \
  -d '{"events": [{"seq": 0, "type": "turn", "turn": {"role": "agent", "text": "Pickup or delivery?", "offset_s": 0.8}}]}'

curl -s -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/sessions/livekit/AJ_tp_8f2k1/close" \
  -d '{"ended_at": "2026-10-15T23:04:40Z"}'
```

```json
{"accepted": 1, "duplicates": 0}
```

| | |
|---|---|
| `seq` | Counts from 0 per session. A resent `seq` is ignored, and events can arrive in any order: they're kept in `seq` order. Events for a session that has closed are ignored. |
| A full post | `POST /v1/workspaces/{ws}/sessions` still works at any point and replaces whatever streamed in. |
| Going idle | An open session with no events for `SOPOPS_SESSION_IDLE_MINUTES` (default 30) is closed as `incomplete` and checked, the next time the workspace opens a session or in the daily job. |
| Auth | All three endpoints take the `ingest` token. |
| Runtime guidance | This is the transport for it in a later version: the pipeline has a hook for each batch of events (`pipeline.session_event`), which does nothing yet. |
| The callback | The SDKs accept it and never call it yet: `on_guidance` on Python's `attach`, `record_session` and `SessionStream`, `onGuidance` on TypeScript's `sopops.stream` options and `elevenLabsSession`. TypeScript's `attach` has none. |

From the SDKs, LiveKit's `attach` posts the session at shutdown in both languages. Python can stream it instead with `stream=True`; in TypeScript, stream it with `sopops.stream(session)`, which returns a `SessionStream`.

```python
from sopops.livekit import attach

ins = await attach(ctx, session, agent="tonys-pizza", stream=True)
```

```ts
import { attach } from "sopops/livekit";

const ins = await attach(ctx, session, { agent: "tonys-pizza" });
```

### Backfill from ElevenLabs or Vapi

Brings in the last 7 days of calls from a platform's API, using the API key stored under `runtime/platforms`, for each agent at head on that platform. An owner or admin can use these. `platform` is `elevenlabs` or `vapi`.

Each call is converted with the same code as the webhook that would have sent it (the ElevenLabs post-call webhook, the Vapi end-of-call report), so it's the same session: the same `external_id`, `config`, `test`, tool calls and metadata.

| | |
|---|---|
| Already in sopops | Skipped. A webhook arriving later for a backfilled call changes nothing. |
| New | Checked like any other session. |
| Still running | Left for its webhook. |

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/capture/{platform}/backfill/preview` | `?days=` (default 7; more counts as 7). Reads the platform now. |
| `POST /v1/workspaces/{ws}/capture/{platform}/backfill` | `{days?}`: start it in the background. `202` with the run. While one is running for the platform, it returns that one. |
| `GET /v1/workspaces/{ws}/capture/{platform}/backfill` | The latest run, or null. |

```sh
curl -s "$SOPOPS_URL/v1/workspaces/harbor-qa/capture/elevenlabs/backfill/preview?days=7"
```

```json
{
  "platform": "elevenlabs",
  "days": 7,
  "key_set": true,
  "error": null,
  "agents": [
    {"agent": "support-line", "platform_id": "ag_42", "found": 86, "already_imported": 14, "to_import": 72, "capped": false, "error": null}
  ],
  "found": 86,
  "already_imported": 14,
  "to_import": 72,
  "cost_per_session_usd": 0.009199,
  "estimated_cost_usd": 0.662
}
```

| Preview field | |
|---|---|
| `key_set`, `error` | Whether a key is saved, and why the platform couldn't be read. An agent whose calls can't be listed (no key, a refusal) has the reason in its own `error`. |
| `found`, `already_imported`, `to_import` | Calls in the window, in total and per agent. |
| `cost_per_session_usd`, `estimated_cost_usd` | `to_import` times this team's model spend per checked session over the last 30 days. Null with no spend yet. |

**A run** is `{id, platform, days, status: running | done | failed, found, imported, skipped, failed, agents, error, created_by, created_at, updated_at, finished_at}`. `skipped` calls were already in sopops; `failed` ones couldn't be read or converted. Each agent has the same counts and its last error.

| Platform | How it reads calls |
|---|---|
| ElevenLabs | `GET /v1/convai/conversations` (by `agent_id` and `call_start_after_unix`, 100 a page), then `GET /v1/convai/conversations/{id}` for each new call. |
| Vapi | `GET /call` (by `assistantId` and `createdAtGt`, 100 a page, then `createdAtLt` the oldest seen), each call whole. |

Requests are spaced out, and a `429` waits for `Retry-After` and tries again. Each agent stops after 20 pages (`capped`). A run that stops moving for 15 minutes (the server restarted) reads as `failed`; starting again picks up the rest.

## The canonical format

```json
{
  "platform": "livekit",
  "external_id": "AJ_tp_8f2k1",
  "channel": "voice",
  "agent": "tonys-pizza",
  "release": 1,
  "version_ref": null,
  "started_at": "2026-10-15T23:04:10Z",
  "ended_at": "2026-10-15T23:04:40Z",
  "partial": false,
  "turns": [
    {"role": "agent", "text": "Thanks for calling Tony's Pizza! Pickup or delivery today?", "offset_s": 0.8},
    {"role": "user", "text": "Delivery please.", "at": 1792105454.2},
    {"role": "tool", "text": "check_delivery_zone", "name": "check_delivery_zone", "offset_s": 15.1}
  ],
  "tool_calls": [
    {"name": "check_delivery_zone", "arguments": {"address": "221 Mercer St"}, "result": {"in_zone": true}, "offset_s": 15.1}
  ],
  "metadata": {"room": "tp-delivery-8f2k1"},
  "test": "haiku-vs-gpt",
  "config": {
    "llm": "anthropic/claude-haiku-4-5",
    "tts": "cartesia/sonic-2",
    "pipeline": {"vad": {"threshold": 0.55, "min_silence_ms": 400}, "endpointing": "semantic"},
    "flags": ["warm_transfer", "barge_in"]
  }
}
```

| Field | Type | |
|---|---|---|
| `platform` | enum | Required. `livekit`, `elevenlabs` or `vapi`, or the transport: `otel`, `import`, `api`. |
| `external_id` | string | Required, up to 256 characters. The platform's id for the session: a LiveKit job id (room names get reused), an ElevenLabs conversation id, a Vapi call id. With `platform`, it's the session's unique key in the workspace. |
| `channel` | enum | `voice` (default) or `text`. |
| `agent` | string | The sop agent id, its platform ref (`livekit:tonys-pizza`) or its platform id on this platform. Matched against the given release (else the live one) and stored as `agent_id`; what was sent is kept as `agent_ref`. |
| `release` | int | The sopops release the agent ran on (`release` from `GET /v1/workspaces/{ws}/agents/{agent}` at session start). An unknown release is dropped with a warning; the session is still stored. |
| `version_ref` | string | The platform's own version of the agent: ElevenLabs `version_id`, Vapi `assistantVersion`. |
| `started_at`, `ended_at` | ISO 8601 | `started_at` defaults to the first turn's time, else when it was received. |
| `partial` | bool | The sender knows the transcript is incomplete. The status is then `incomplete`. |
| `turns` | list | In order. Fields below. |
| `tool_calls` | list | `{name, arguments, result, is_error, call_id, offset_s or at}`. Other fields are kept. |
| `metadata` | object | Anything else. `sopops_*` keys (flat, or nested under `sopops`) are read as `test` (`sopops_test`) and config (`sopops_llm`, and so on), and win over the fields below. |
| `test` | string | An A/B test this session is part of (optional, up to 128 characters). |
| `config` | object | What the agent ran with: scalars, and whole objects or arrays. Fields below. |

**`turns`.** `role` is `agent`, `user`, `tool` or `system`; `assistant`, `bot`, `human`, `customer`, `function` and similar are mapped. `text` is the words. Time is `offset_s` (seconds since the start) or `at` (ISO 8601, or Unix seconds or milliseconds), which becomes `offset_s`. Other fields (`interrupted`, `name`) are kept.

### `config`

`llm`, `stt`, `tts` (`<provider>/<model>`), `voice`, `prompt` (a template built outside sopops), `version`, or any other setting.

| | |
|---|---|
| A setting | A string, number or boolean, or a whole object or array: a voice pipeline's settings, a page of feature flags, a nested section. |
| Nested content | Kept as sent, keys and all. Only the top-level keys are lowercased and trimmed. |
| Left out | A setting that is null, blank, `unknown`, or an empty object or array, and anything JSON can't hold. |
| `prompt` | Sent as text, it's stored once and its hash goes in the config. |
| `config_hash` | Sessions with the same agent, release and hash are one version ([A/B tests](../ab-tests.md)). |
| Filled for you | ElevenLabs and Vapi captures read the agent's settings; the Python and TypeScript LiveKit helpers read the `AgentSession`. |

| Limit | |
|---|---|
| 32 settings | Beyond that, the rest are left out. |
| 64 characters | A key. |
| 1,024 characters | A string setting. A `prompt` may run to 65,536. |
| 8,192 characters | An object or array setting, as compact JSON with keys sorted. |
| 8 levels | How deep an object or array setting may nest. A flat one is 1. |

A setting over a limit is left out with a warning in the ingest response, and the session is still stored.

### Processing and status

Processing (`process_session`) normalizes turns: canonical roles, `offset_s` from `at`, text trimmed, empty turns dropped (except tool turns), a turn that repeats the one before it dropped, sorted by time. Then it routes and checks the session ([SOP checks](checks-and-review.md#sop-checks)).

| Status | |
|---|---|
| `open` | Streaming in. Not checked until it closes. |
| `received` | Stored and normalized, not checked (yet). With no model configured it stays here, with `status_reason` saying so. |
| `routed` | The procedures that applied are known; their checks are running. |
| `checked` | Every procedure that applied was checked; `result` is set. |
| `excluded` | Nothing to check: no procedure applied, or every one that did was cut short (the session ended before it started or finished). `status_reason` says which. |
| `incomplete` | No turns, or the sender said the transcript is incomplete (or it was streamed and went idle). An incomplete session with turns is still checked, and keeps this status. This isn't the procedure result `partial`. |
| `failed` | Processing failed; `status_reason` says where (`normalize`, `route`, `check <procedure>`). |

## Import a file

```sh
sopops import examples/import-sessions/restaurant-sessions.jsonl --workspace test
```

Reads one session per line in the canonical format (`-` reads stdin) straight into the database named by `DATABASE_URL`, with no token. It runs `process_session` for each new or changed session.

Lines that don't parse are reported by line number and skipped, and the command exits 1 if any did. Importing the same file again changes nothing. [`examples/import-sessions/restaurant-sessions.jsonl`](../../examples/import-sessions) has ten made-up sessions for the test workspace.
