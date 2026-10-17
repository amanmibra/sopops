# sopops API

Every endpoint. Paths below are under `/v1/workspaces/<ws>` unless they start with `/v1/`. The OpenAPI spec at `/openapi.json` has every field; `/docs` lets you try them.

There is no authentication yet beyond the workspace token (session ingest, and prompt fetches once the workspace has one): keep sopops on a private network.

Each operation's `operationId` is its route name (`list_workspaces`, `get_agent`, `config_put_procedure`), so generated clients have readable function names; the web UI's typed client (`web/src/client`) is generated this way. Paths outside `/v1` (and `/docs`, `/openapi.json`) serve the web UI.

## Errors

Errors that come from sopc return `422 {"valid": false, "issues": [...]}`. Each issue has sopc's `code`, `message`, `path` and `severity`, plus where it belongs in the forms:

| Field | |
|---|---|
| `kind` | `agent`, `instruction`, `procedure`, `group` or `settings` |
| `id` | Which one |
| `field` | As the `/config` endpoints name it, e.g. `blocks`, `steps` |
| `index` | Which entry of a list field |
| `text` | The problem in plain words, e.g. "Lists 'nope', which isn't a shared instruction, procedure or group." |

A `404` or `409` has `{"detail": "..."}`.

## Config: the forms

What the UI edits. Each item is stored as a sopc file (sopops writes it, then reads it back with `sopc export` to check it holds exactly what was sent), so git publishing and exports keep working. Saves are drafts: nothing reaches agents until a release. Ids can't be renamed yet.

| Endpoint | What it does |
|---|---|
| `GET …/config` | Every item at head: `{settings, agents, instructions, procedures, groups}`, each with its `version`. Instructions, procedures and groups have `used_by: [{agent, via}]` (`via`: the group the agent lists it through, or null). `422` with `old_format` issues if the workspace is in the sopc v0.0.8 format, or `removed_field` issues if it still sets `locked` (sopc v0.0.9); `POST …/migrate` converts either. |
| `GET/POST …/config/agents` | List agents; create one: `{id, platform, platform_id, context, blocks, variables, author?, note?}` (`409` if it exists). |
| `GET/PUT/DELETE …/config/agents/<id>` | One agent; create or replace it; delete it. |
| `POST …/config/agents/<id>/blocks` | `{block, author?, note?}`: add a shared instruction, procedure or group at the end of the agent's blocks. `409` if the agent already lists it or gets it through a group. |
| `DELETE …/config/agents/<id>/blocks/<block>` | Take a block out of the agent's blocks. `409` if the agent gets it through a group. |
| `POST …/config/agents/<id>/preview` | The agent's fields, unsaved: `{valid, issues, prompt, hash, blocks, release, released_prompt}`. Compiles with the rest of head; saves nothing. |
| `…/config/instructions[/<id>]` | Shared instructions: `{id, text}`. Deleting one takes it out of every agent and group that lists it. |
| `…/config/procedures[/<id>]` | Procedures (SOPs): `{id, name, goal, when, guidance, steps: [{text, tool, required}], never, warning_signs, delivery: prompt\|auto\|tool}`. New ones are written as Markdown; one that came from git keeps its format unless Markdown can't hold an edit, then it becomes YAML. Deleting one takes it out of every agent and group. |
| `…/config/groups[/<id>]` | Groups, kept in `sopc.yaml`: `{id, blocks}`. Deleting one lists its blocks in its place wherever it was used, so no prompt changes. |
| `GET/PUT …/config/settings` | `{variables, procedures_heading}`. |
| `GET …/config/<kind>/<id>/history` | Every version, newest first. Groups and settings list only the versions of `sopc.yaml` that changed them. |
| `GET …/config/<kind>/<id>/versions/<n>` | An older version, as fields. One that set `locked` is read as `sopc migrate` converts it; one saved in the v0.0.8 format comes back with `legacy: true` and its `content`. |

Instructions, procedures and groups share one set of names.

## Files

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces` | Workspaces and their current release. |
| `GET …/files` | Head: every file with its version. `?content=true` includes content (export to git). |
| `POST …/files` | `{changes: {path: content \| null}, author?, note?}`: edit or delete files atomically. Rejected if the result doesn't compile. |
| `PUT …/files` | `{files, author?, note?}`: replace head; files not listed are deleted. |
| `GET …/files/<path>?version=N` | A file at head or at a version. |
| `GET …/history/<path>` | Every version of a file. |

Paths under `build/` in uploads are ignored; absolute paths and `..` are rejected.

## Releases

| Endpoint | What it does |
|---|---|
| `GET …/draft` | Unpublished changes: files and `items` changed since the current release (each with its fields `before` and `after`), and agents added, changed, removed or unchanged, with the draft and released prompts, and `lint`: what `sopc lint` finds in the draft (duplicated or conflicting instructions; advisory, publishing isn't blocked). A release in an older format is compared as `sopc migrate` converts it. |
| `POST …/releases` | `{author?, note?}`: release head and serve it (returns the current release if nothing changed). |
| `GET …/releases`, `GET …/releases/<n>` | Release history; one release with every agent's prompt. |
| `POST …/releases/<n>/activate` | Serve an older release (roll back). |
| `POST …/publish` | `{files, author?, note?}`: replace + release in one call, for CI. |
| `POST …/migrate` | `{apply?, author?, note?}`: convert a workspace stored in an older sopc format with `sopc migrate`: the v0.0.8 format, or `locked` (v0.0.9), which it removes. Returns `{needed, plan, applied, changed}`; with `apply`, the converted files are written as a draft. |

## Serving

| Endpoint | What it does |
|---|---|
| `GET …/agents` | Agents in the current release (`?release=N` for another). |
| `GET …/agents/<agent>` | **Call start.** `{agent, platform_ref, release, hash, prompt, tools, tool_sops, components}`. `?release=N` pins a release (A/B). Logs a fetch. |
| `GET …/agents/<agent>/prompt` | Just the prompt as text, with `X-Sopc-Release` and `X-Sopc-Hash`. Logs a fetch. |
| `GET …/agents/<agent>/sops/<id>` | What a `get_sop` tool returns, for SOPs with `delivery: auto` or `tool`. |
| `GET …/fetches?agent=` | Which release and prompt hash each agent was served, newest first. |

`GET …/agents/<agent>`, `…/prompt` and `…/sops/<id>` take the workspace's token (the ingest token, also called the runtime token) as `Authorization: Bearer <token>` once the workspace has one, and answer `401` without it. A workspace without a token serves them openly (local mode).

`<agent>` is the sopc agent id (`tonys-pizza`) or its platform ref (`livekit:tonys-pizza`). `components` lists each file the prompt was built from, in prompt order, at its version: `agent`, `instruction`, `sop`, plus `sopc.yaml` as `config` (`base` in releases made before sopc v0.0.9).

## Runtime delivery

How each agent gets its prompt: **pull** (the agent, or its platform's call-start webhook, fetches it) or **push** (sopops writes it into the agent on ElevenLabs or Vapi when a release goes live), or both.

| Endpoint | What it does |
|---|---|
| `GET …/runtime/delivery` | Every agent at head: `{agent, platform, platform_id, mode, modes}`. No setting means `pull`; LiveKit agents only pull. |
| `PUT …/runtime/delivery/<agent>` | `{mode: pull \| push \| both}`. `400` for a mode the platform doesn't allow. |
| `GET …/runtime/platforms` | For `elevenlabs` and `vapi`: `{api_key, webhook_secret}`, each `{set, hint, created_at}`. Never the values. |
| `PUT …/runtime/platforms/<platform>/api-key` | `{value}`: store the platform API key, encrypted with `SOPOPS_SECRET_KEY` (Fernet). `503` if that isn't set. |
| `PUT …/runtime/platforms/<platform>/webhook-secret` | `{value?}`: store a webhook secret; without a value one is made and returned once as `secret`. |
| `DELETE …/runtime/platforms/<platform>/<api-key \| webhook-secret>` | Remove it. |
| `POST …/runtime/platforms/<platform>/test` | `{platform_id?}`: read one agent with the stored key (a GET; default: the first agent at head on that platform). `{ok, platform_id, message}`. |
| `GET …/releases/<n>/pushes` | Each push-mode agent's latest push of the release: `{agent, platform, platform_id, status: ok \| unchanged \| error, version_id, error, attempts, at}`. |
| `POST …/releases/<n>/pushes/retry` | `{agents?}`: push again (default: the agents whose last push failed). `409` unless the release is live. |
| `POST …/runtime/served` | `{platform, call_id, agent, release}` with the runtime token: a client that fetched the prompt itself reports the call it served, so the session links to the release. |
| `POST …/runtime/elevenlabs/conversation-init` | ElevenLabs' conversation-initiation webhook (`?agent=`, `?release=` optional; default: the agent named by `agent_id` in the body, live release). Returns `{type: "conversation_initiation_client_data", dynamic_variables: {sopops_release, sopops_agent}, conversation_config_override: {agent: {prompt: {prompt}}}}`. |
| `POST …/runtime/vapi/assistant-request` | Vapi's server URL (`?agent=` needed if the release has more than one Vapi agent; `?release=` optional). For `assistant-request`: `{assistantId, assistantOverrides: {model, variableValues: {sopops_prompt, sopops_release, sopops_agent}, metadata}}`, where `model` is the assistant's own (read with the stored key, cached 5 minutes) with the system message replaced; without a key, only `variableValues` (use `{{sopops_prompt}}` as the system message). Other message types get `{}`. |

**Push.** Creating a release (`POST …/releases`, `…/publish`) or activating one pushes it to every push-mode agent, after the response. ElevenLabs: `GET /v1/convai/agents/<id>`, then `PATCH` with `{conversation_config: {agent: {prompt: {prompt}}}}`; an agent whose workflow has nodes besides start and end is refused, since its main prompt isn't the whole prompt. Vapi: `GET /assistant/<id>`, then `PATCH {model}` with every model field kept and the one system message replaced (added if there's none; refused if there are several). A prompt that's already live is recorded as `unchanged` without a write. `version_id` is ElevenLabs' `version_id` or Vapi's `latestVersion` when returned.

**Webhook authentication.** A request is accepted with the platform's webhook secret, as `Authorization: Bearer`, `X-Sopops-Secret` or `X-Vapi-Secret`, or as an HMAC-SHA256 signature: ElevenLabs' `ElevenLabs-Signature: t=…,v0=…` over `"{t}.{body}"` (at most 30 minutes old), or Vapi's `x-signature` (hex) and `x-timestamp` over `"{timestamp}.{body}"` (within 5 minutes). The runtime token as a bearer works too. A workspace with neither a webhook secret nor a token accepts them unauthenticated (local mode).

**Linking sessions.** Each prompt served by a webhook, or reported to `…/runtime/served`, is kept for 7 days keyed by the call id: Vapi's `call.id`, ElevenLabs' `conversation_id` or `call_sid`. A session ingested without a `release` takes it from there, matching `external_id`, `metadata.call_sid` or `metadata.phone_call.call_sid`.

## Stateless

Nothing is stored.

| Endpoint | What it does |
|---|---|
| `POST /v1/validate`, `/v1/render`, `/v1/lint` | `{files}`: sopc's checks, compiled prompts, and duplicate or conflicting instructions. |
| `POST /v1/migrate` | `{files}`: a folder in an older sopc format (v0.0.8, or with `locked`), converted with `sopc migrate`. Returns `{migrated, plan, files}`. |

## Sessions

A session is one conversation an agent had: a voice call or a text chat. Sessions come from LiveKit (the [helper](helpers/livekit)), ElevenLabs or Vapi, or from OpenTelemetry, a file import or the API, and all of them use one canonical format.

| Endpoint | What it does |
|---|---|
| `POST …/sessions` | Ingest one session (below), with `Authorization: Bearer <ingest token>`. An upsert on `(platform, external_id)`: `201 {id, created: true, updated: false, status, warnings}` for a new session; `200` with `updated: true` if its content changed; `200` with both false for an identical re-send, which changes nothing. A new or changed session runs `process_session`. `401` without the workspace's token. |
| `GET …/sessions` | Newest first by start time: `{items, next_cursor}`. Filters: `agent` (sopc id, or the agent as sent), `channel`, `status`, `platform`, `since`, `until` (ISO 8601, on `started_at`), plus `cursor` (the previous page's `next_cursor`) and `limit` (default 50, max 200). |
| `GET …/sessions/<id>` | One session with `turns`, `tool_calls` and `metadata`. |
| `GET …/sessions/stream` | Server-sent events: `event: session` with `data: {type: created \| updated, workspace, id, status}` each time a session is ingested or processed. A comment every 15s keeps it open. |
| `GET …/ingest-token` | `{exists, prefix, created_at}`. The token itself is only shown when made. |
| `POST …/ingest-token` | Make the workspace's ingest token, or replace it (the old one stops working): `{token, prefix, created_at}`. Stored as a hash. |
| `DELETE …/ingest-token` | Delete it; ingest is refused until a new one is made. |

Ingest needs the token, and so do prompt fetches once it exists (see Serving); the rest of the API is open, as above.

### The canonical format

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
  "metadata": {"room": "tp-delivery-8f2k1"}
}
```

| Field | |
|---|---|
| `platform` | Required. `livekit`, `elevenlabs` or `vapi`, or the transport: `otel`, `import`, `api`. |
| `external_id` | Required. The platform's id for the session: a LiveKit job id (room names get reused), an ElevenLabs conversation id, a Vapi call id. With `platform`, its unique key in the workspace. |
| `channel` | `voice` (default) or `text`. |
| `agent` | The sopc agent id, its platform ref (`livekit:tonys-pizza`) or its platform id on this platform. Matched against the given release (else the live one) and stored as `agent_id`; what was sent is kept as `agent_ref`. |
| `release` | The sopops release the agent ran on (`release` from `GET …/agents/<agent>` at session start). An unknown release is dropped with a warning; the session is still stored. |
| `version_ref` | The platform's own version of the agent: ElevenLabs `version_id`, Vapi `assistantVersion`. |
| `started_at`, `ended_at` | ISO 8601. `started_at` defaults to the first turn's time, else when it was received. |
| `partial` | The sender knows the transcript is incomplete; the status is then `partial`. |
| `turns` | In order. `role`: `agent`, `user`, `tool` or `system` (`assistant`, `bot`, `human`, `customer`, `function` and similar are mapped). `text`. Time as `offset_s` (seconds since the start) or `at` (ISO 8601, or Unix seconds or milliseconds), which becomes `offset_s`. Other fields (`interrupted`, `name`, …) are kept. |
| `tool_calls` | `{name, arguments, result, is_error, call_id, offset_s or at}`; other fields are kept. |
| `metadata` | Anything else, as an object. |

Processing (`process_session`) normalizes turns: canonical roles, `offset_s` from `at`, text trimmed, empty turns dropped (except tool turns), a turn that repeats the one before it dropped, sorted by time. The status is then `received`, or `partial` if there are no turns or the sender said so, or `failed` with a `status_reason`. `routed`, `checked` and `excluded` come with SOP checks.

### Importing a file

`sopops import FILE.jsonl --workspace WS` reads one session per line in this format (`-` reads stdin) straight into the database named by `DATABASE_URL`, with no token, and runs `process_session` for each new or changed one. Lines that don't parse are reported by line number and skipped; it exits 1 if any did. Importing the same file again changes nothing. [`examples/restaurant-sessions.jsonl`](examples/restaurant-sessions.jsonl) has ten made-up sessions for the test workspace.
