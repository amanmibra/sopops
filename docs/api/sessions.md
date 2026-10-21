# Sessions

A session is one conversation an agent had: a voice call or a text chat. Sessions come from LiveKit (the SDKs' adapters, [Python](../../sdks/python) and [Node](../../sdks/node)), ElevenLabs or Vapi, or from OpenTelemetry, a file import or the API. All of them use one [canonical format](#the-canonical-format).

## Send and read sessions

| Endpoint | What it does |
|---|---|
| `POST /v1/workspaces/{ws}/sessions` | Ingest one session, with `Authorization: Bearer <token>` (scope `ingest`). See below. |
| `GET /v1/workspaces/{ws}/sessions` | Newest first by start time: `{items, next_cursor}`. Each has `status` and `result` (the worst procedure result, once checked). Filters below. |
| `GET /v1/workspaces/{ws}/sessions/{id}` | One session with `turns`, `tool_calls`, `metadata` and `checks` ([SOP checks](checks-and-review.md#sop-checks)). |
| `GET /v1/workspaces/{ws}/sessions/stream` | Server-sent events: `event: session` with `data: {type: created \| updated, workspace, id, status}` each time a session is ingested or processed. A comment every 15s keeps it open. |

Ingest needs a token with the `ingest` scope ([Tokens](teams-and-access.md#tokens)). The rest follows the usual [access rules](teams-and-access.md#sign-in-and-access).

**Ingest responses.** `POST /sessions` is an upsert on `(platform, external_id)`:

| Response | When |
|---|---|
| `201 {id, created: true, updated: false, status, warnings}` | A new session. |
| `200` with `updated: true` | Its content changed. |
| `200` with `created` and `updated` both false | An identical re-send, which changes nothing. |
| `401` | No valid token for the workspace. |
| `403` | A token without the `ingest` scope. |

A new or changed session runs `process_session`.

**List filters.**

| Filter | |
|---|---|
| `agent` | The sopc id, or the agent as sent. |
| `channel`, `status`, `result`, `platform`, `release` | Exact match. |
| `since`, `until` | ISO 8601, on `started_at`. |
| `cursor`, `limit` | `cursor` is the previous page's `next_cursor`. `limit` defaults to 50, max 200. |

Drill-down filters, the ones [Insights](insights.md) links to:

| Filter | |
|---|---|
| `procedure` | The router considered it. |
| `procedure_result` | That procedure's result. |
| `rule` | Any wording of the rule (its [lineage](checks-and-review.md#rule-lineage)), with optional `rule_status` (repeatable). |
| `pattern` | A rule pattern includes its sub-patterns. |
| `theme` | |
| `finding` | true: with any finding; false: with none. |
| `eligible` | The sessions Insights rates: checked or incomplete, with a result. |
| `suppressed` | false: the finding filters leave suppressed findings out, as Insights does; true: only them. |

### Streaming (optional)

A sender can also stream a session as it happens, then close it. Nothing is checked until it closes, so a streamed session ends up the same as one posted at the end. The SDKs do this with `stream=True` (Python `attach`) or `stream: true` (Node). It's the transport for runtime guidance in a later version: the pipeline has a hook for each batch of events (`pipeline.session_event`), which does nothing yet.

| Endpoint | What it does |
|---|---|
| `POST /v1/workspaces/{ws}/sessions/open` | `{platform, external_id, channel?, agent?, release?, version_ref?, started_at?, metadata?, test?, config?}`. Creates the session with status `open` (`201`), or returns the existing one (`200`). |
| `POST /v1/workspaces/{ws}/sessions/{platform}/{external_id}/events` | `{events: [{seq, type: turn \| tool_call, turn? \| tool_call?}]}`, up to 500. Returns `{accepted, duplicates}`. `404` if the session was never opened. |
| `POST /v1/workspaces/{ws}/sessions/{platform}/{external_id}/close` | `{ended_at?, partial?, test?, config?}`. `config` is merged over the one sent at open. Closes the session with what streamed in and runs `process_session`. |

- `seq` counts from 0 per session. A resent `seq` is ignored, and events can arrive in any order; they're kept in `seq` order. Events for a session that has closed are ignored.
- A full post to `POST /v1/workspaces/{ws}/sessions` still works at any point and replaces whatever streamed in.
- An open session with no events for `SOPOPS_SESSION_IDLE_MINUTES` (default 30) is closed as `incomplete` and checked. That happens the next time the workspace opens a session, or in the daily job.
- All three endpoints take the `ingest` token.

### Backfill from ElevenLabs or Vapi

Brings in the last 7 days of calls from a platform's API, using the API key stored under `runtime/platforms`, for each agent at head on that platform. An owner or admin can use these.

Each call is converted with the same code as the webhook that would have sent it (the ElevenLabs post-call webhook, the Vapi end-of-call report), so it's the same session: the same `external_id`, `config`, `test`, tool calls and metadata. Calls already in sopops are skipped, and a webhook arriving later for a backfilled call changes nothing. New sessions are checked like any other. Calls still running are left for their webhook.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/capture/{platform}/backfill/preview` | `?days=` (default 7; more counts as 7). Reads the platform now. See below. |
| `POST /v1/workspaces/{ws}/capture/{platform}/backfill` | `{days?}`: start it in the background. `202` with the run (below). While one is running for the platform, it returns that one. |
| `GET /v1/workspaces/{ws}/capture/{platform}/backfill` | The latest run, or null. See below. |

`platform` is `elevenlabs` or `vapi`.

**Preview** returns `{platform, days, key_set, error, agents, found, already_imported, to_import, cost_per_session_usd, estimated_cost_usd}`. Each agent is `{agent, platform_id, found, already_imported, to_import, capped, error}`. An agent whose calls can't be listed (no key, a refusal) has the reason in `error`. The cost is `to_import` times this team's model spend per checked session over the last 30 days, or null with none.

**A run** is `{id, platform, days, status: running | done | failed, found, imported, skipped, failed, agents, error, created_by, created_at, updated_at, finished_at}`. `skipped` calls were already in sopops; `failed` ones couldn't be read or converted. Each agent has the same counts and its last error.

**How it reads each platform:**
- ElevenLabs: `GET /v1/convai/conversations` (by `agent_id` and `call_start_after_unix`, 100 a page), then `GET /v1/convai/conversations/{id}` for each new call.
- Vapi: `GET /call` (by `assistantId` and `createdAtGt`, 100 a page, then `createdAtLt` the oldest seen), each call whole.

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
  "config": {"llm": "anthropic/claude-haiku-4-5", "tts": "cartesia/sonic-2"}
}
```

| Field | |
|---|---|
| `platform` | Required. `livekit`, `elevenlabs` or `vapi`, or the transport: `otel`, `import`, `api`. |
| `external_id` | Required. The platform's id for the session: a LiveKit job id (room names get reused), an ElevenLabs conversation id, a Vapi call id. With `platform`, it's the session's unique key in the workspace. |
| `channel` | `voice` (default) or `text`. |
| `agent` | The sopc agent id, its platform ref (`livekit:tonys-pizza`) or its platform id on this platform. Matched against the given release (else the live one) and stored as `agent_id`; what was sent is kept as `agent_ref`. |
| `release` | The sopops release the agent ran on (`release` from `GET /v1/workspaces/{ws}/agents/{agent}` at session start). An unknown release is dropped with a warning; the session is still stored. |
| `version_ref` | The platform's own version of the agent: ElevenLabs `version_id`, Vapi `assistantVersion`. |
| `started_at`, `ended_at` | ISO 8601. `started_at` defaults to the first turn's time, else when it was received. |
| `partial` | The sender knows the transcript is incomplete. The status is then `incomplete`. |
| `turns` | In order. See below. |
| `tool_calls` | `{name, arguments, result, is_error, call_id, offset_s or at}`. Other fields are kept. |
| `metadata` | Anything else, as an object. `sopops_*` keys are read as `test` (`sopops_test`) and config (`sopops_llm`, ...), and win over the fields below. |
| `test` | An A/B test the session is part of (optional, up to 128 characters). |
| `config` | What the agent ran with. See below. |

**`turns`.** `role` is `agent`, `user`, `tool` or `system`; `assistant`, `bot`, `human`, `customer`, `function` and similar are mapped. `text` is the words. Time is `offset_s` (seconds since the start) or `at` (ISO 8601, or Unix seconds or milliseconds), which becomes `offset_s`. Other fields (`interrupted`, `name`, …) are kept.

**`config`.** `llm`, `stt`, `tts` (`<provider>/<model>`), `voice`, `prompt` (a template built outside sopops), `version`, or any other setting; scalars only.
- Limits: up to 32 settings, keys up to 64 characters, values up to 1,024 (a prompt up to 65,536). A setting over a limit is left out with a warning.
- A prompt is stored once, and its hash goes in the config.
- Stored with `config_hash`: sessions with the same agent, release and hash are one version ([A/B tests](../ab-tests.md)).
- ElevenLabs and Vapi captures fill it from the agent's settings; the Python and TypeScript LiveKit helpers fill it from the `AgentSession`.

### Processing and status

Processing (`process_session`) normalizes turns: canonical roles, `offset_s` from `at`, text trimmed, empty turns dropped (except tool turns), a turn that repeats the one before it dropped, sorted by time. Then it routes and checks the session ([SOP checks](checks-and-review.md#sop-checks)).

| Status | |
|---|---|
| `received` | Stored and normalized, not checked (yet). With no model configured it stays here, with `status_reason` saying so. |
| `routed` | The procedures that applied are known; their checks are running. |
| `checked` | Every procedure that applied was checked; `result` is set. |
| `excluded` | Nothing to check: no procedure applied, or every one that did was cut short (the session ended before it started or finished). `status_reason` says which. |
| `incomplete` | No turns, or the sender said the transcript is incomplete (or it was streamed and went idle). An incomplete session with turns is still checked, and keeps this status. This isn't the procedure result `partial`. |
| `failed` | Processing failed; `status_reason` says where (`normalize`, `route`, `check <procedure>`). |

## Import a file

`sopops import FILE.jsonl --workspace WS` reads one session per line in the canonical format (`-` reads stdin) straight into the database named by `DATABASE_URL`, with no token. It runs `process_session` for each new or changed session.

Lines that don't parse are reported by line number and skipped, and the command exits 1 if any did. Importing the same file again changes nothing. [`examples/import-sessions/restaurant-sessions.jsonl`](../../examples/import-sessions) has ten made-up sessions for the test workspace.
