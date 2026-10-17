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
| `GET …/sessions` | Newest first by start time: `{items, next_cursor}`. Each has `status` and `result` (the worst procedure result, once checked). Filters: `agent` (sopc id, or the agent as sent), `channel`, `status`, `result`, `platform`, `since`, `until` (ISO 8601, on `started_at`), `release`, plus `cursor` (the previous page's `next_cursor`) and `limit` (default 50, max 200). Drill-down filters (what [Insights](#insights) links to): `procedure` (the router considered it), `procedure_result`, `rule` with optional `rule_status` (repeatable), `pattern` (a rule pattern includes its sub-patterns), `theme`, `finding` (true: with any finding; false: with none), `eligible` (the sessions Insights rates: checked or partial, with a result). |
| `GET …/sessions/<id>` | One session with `turns`, `tool_calls`, `metadata` and `checks` ([SOP checks](#sop-checks)). |
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

Processing (`process_session`) normalizes turns: canonical roles, `offset_s` from `at`, text trimmed, empty turns dropped (except tool turns), a turn that repeats the one before it dropped, sorted by time. Then it routes and checks the session ([SOP checks](#sop-checks)).

| Status | |
|---|---|
| `received` | Stored and normalized, not checked (yet). With no model configured it stays here, with `status_reason` saying so. |
| `routed` | The procedures that applied are known; their checks are running. |
| `checked` | Every procedure that applied was checked; `result` is set. |
| `excluded` | Nothing to check: no procedure applied, or every one that did was cut short (the session ended before it started or finished). `status_reason` says which. |
| `partial` | No turns, or the sender said the transcript is incomplete. A partial session with turns is still checked, and keeps this status. |
| `failed` | Processing failed; `status_reason` says where (`normalize`, `route`, `check <procedure>`). |

### Importing a file

`sopops import FILE.jsonl --workspace WS` reads one session per line in this format (`-` reads stdin) straight into the database named by `DATABASE_URL`, with no token, and runs `process_session` for each new or changed one. Lines that don't parse are reported by line number and skipped; it exits 1 if any did. Importing the same file again changes nothing. [`examples/restaurant-sessions.jsonl`](examples/restaurant-sessions.jsonl) has ten made-up sessions for the test workspace.

## SOP checks

Each session is checked against the procedures its agent lists (directly or through a group) in the release it ran on, or the live release if it didn't say. Rules are identified by sopc's stable rule ids (`sopc export`, `lock.json`), so results line up across releases.

`GET …/sessions/<id>` returns `checks`, one per procedure the router considered:

| Field | |
|---|---|
| `procedure_id`, `procedure_name`, `procedure_version`, `release_id` | What was checked: the procedure file's version in that release. |
| `applicable` | Whether the procedure applied to the session. |
| `excluded_reason` | Applied, but the session ended before the procedure started or finished, so it isn't checked. |
| `route_reason` | The router's one-sentence reason. |
| `result` | `deviated`, `partial`, `followed` or `not_measurable`; null unless checked. |
| `goal_reached`, `goal_reason` | Whether the procedure's goal was reached (null: unclear). |
| `steps_applicable`, `steps_done`, `nevers_violated` | Counts. Done includes out of order. |
| `model`, `prompt_version` | What produced it. |
| `rules` | `[{rule_id, kind, index, text, status, evidence: [{turn, quote, source}], reason, finding}]`, steps then nevers then warning signs. `turn` indexes `turns`; `source` is `transcript` or `tool_calls`. |

**Rule statuses.**

| Kind | Statuses |
|---|---|
| Step | `done`; `out_of_order` (done, but after a later step: the fewest steps that explain the order of their first evidence turns); `skipped` (the session went past where it belonged without it); `not_reached` (the session ended before it); `not_applicable` (its condition never arose). |
| Never | `violated` or `ok`. |
| Warning sign | `present` (the situation came up and was handled as the sign says), `unheeded` (it came up, the sign says what to do, and the agent didn't) or `absent`. A condition, never a pass or fail; `unheeded` is still "present" in every sign-present count. |

A step with `tool: x` and `required` is checked against the session's `tool_calls` when it has any: called is `done`, not called is `skipped` (unless the session never reached the step or it didn't apply). Without tool calls the transcript decides, and the evidence says so.

**Procedure result.**

| Result | When |
|---|---|
| `deviated` | A never rule was violated, a required step was skipped, or a step was skipped and the goal wasn't reached (or it's unclear). |
| `partial` | Otherwise: a step out of order or not reached, or a step that isn't required was skipped but the goal was reached anyway (leniency: the agent got there another way, so it's a gap, not a deviation). |
| `followed` | Every applicable step done, in order, and no never rule violated. |
| `not_measurable` | Nothing to judge: no applicable step and no never rule violated, or the agent never spoke. Left out of rates. |

A session's `result` is the worst of its procedures' (deviated, then partial, followed, not measurable).

**Findings** are the rule results with status `skipped`, `violated`, `out_of_order` or `unheeded` (`finding: true`): things the agent did or didn't do that someone may want to fix. `not_reached` isn't one (the session ended), and neither is a warning sign that was present and handled. A `violated` never rule always quotes the agent's own words; one the model flagged without such a quote is stored as `ok`, with the reason saying so. A skipped step that leniency kept from making its procedure deviated is still a finding. Findings are stored in their own table and grouped into [patterns](#patterns-and-themes).

Re-running a session replaces its results; a run for content that has since been re-sent writes nothing.

## Patterns and themes

Findings are grouped so the same mistake shows up once, with its count, instead of once per session.

- **Rule pattern** (`kind: rule`): every finding on one rule. Created with the rule's first finding; named from the rule ("Step 6 missed", "Never rule 2 broken", "Warning sign 4 not acted on").
- **Failure modes** (`kind: sub`): once a rule has 8 findings, the reasoning model splits its findings into at most 5 named ways the agent failed it, plus an `other` sub-pattern (`kind: other`) for findings that fit none. New findings on a split rule are assigned by the fast model to one of its failure modes, or to `other`.
- **Reclustering**: a rule is split again when 25 findings arrived since its last clustering, or when `other` holds more than 25% of its findings (with at least 5 new since), plus a daily cron. New clusters are matched to the old ones by member overlap (Jaccard over finding ids, at least 0.3), so a matched failure mode keeps its id and name (and later, its verdicts). A failure mode that matches nothing is `retired`, not deleted.
- **Themes**: the reasoning model groups a procedure's rule patterns by the phase of the session where they happen (opening, taking the order, closing...), in session order. Existing theme names and ids are kept whenever they still fit.

A finding's `pattern_id` is its most specific pattern. The pipeline assigns findings right after a session is checked (inline, or the Hatchet `sopops_patterns` workflow, one run at a time per workspace); due reclustering runs right after (inline) or in `sopops_recluster`. The daily cron is `sopops_recluster_daily` (`SOPOPS_RECLUSTER_CRON`, default `0 6 * * *`); without Hatchet, run `sopops recluster` from cron.

| Endpoint | What it does |
|---|---|
| `GET …/patterns` | `{items}`: rule patterns with their `sub_patterns`, counted in the window (default 365 days; same window filters as Insights). Filters: `procedure`, `rule`. |
| `GET …/patterns/<id>` | One pattern: the rule it's about, its failure modes (for a rule pattern), example phrasings and its sessions in the window. |
| `POST …/patterns/recluster` | `{procedure?, rule?, force?}`: assign unassigned findings, recluster what's due (every rule with 8+ findings with `force`), regroup themes. Calls the models, synchronously. `{assigned, reclustered, themed}`. |

`sopops recluster [--workspace WS] [--procedure P] [--rule R] [--force]` does the same from the command line.

## Insights

Every number comes from one module (`sopops.insights`) with one set of definitions, computed on the fly:

| Term | Definition |
|---|---|
| Window | `[start, end]` on the session's start time, both ends inclusive, at most 366 days. `window=24h\|7d\|30d\|90d\|365d` (default 7d) or `start` and `end`. The prior window is the same length right before it. |
| Eligible session | Status `checked` or `partial`, with a result. In-progress and failed sessions are counted, never rated; excluded sessions are counted separately. |
| Session outcome | The worst result of its procedure checks in scope (only the `procedure` filter's, if set). **Followed end to end**: followed. **Needs attention**: deviated. |
| Pass rate (procedure) | Checks followed / measurable checks (deviated, partial or followed); `not_measurable` checks are never rated. |
| Pass rate (rule) | Steps: done / (done + skipped + out of order). Never rules: ok / (ok + violated). `n` is that denominator; `not_reached` and `not_applicable` are shown as a share, never counted. Warning signs are never in a pass rate: they are present (`present` or `unheeded`) or absent. |
| Percentages | Whole numbers, rounded half up, with their `n`; null (shown "—") when the denominator is 0. Rate deltas are in points; count deltas in %. |
| Comparisons | 95% Wilson interval per rate. A side with n < 10 is `thin` and never flagged; `regressed` / `improved` need a change of 5 points or more and intervals that don't overlap. Releases are compared by the release each session ran, never by date. |

All take the window filters plus `agent`, `procedure`, `release` (repeatable) and `channel`.

| Endpoint | What it returns |
|---|---|
| `GET …/insights/overview` | Tiles (current, prior, deltas): sessions checked, followed end to end, needs attention, excluded, procedure pass rate, goal rate. Trend per day (windows up to 31 days) or week, with release markers. Procedure scoreboard: checks, pass rate and delta, top failing rules, never violations, open patterns, excluded. |
| `GET …/insights/procedures/<id>` | Tiles, trend, the rule ladder (per rule: n, pass, fail and skip rates, not-counted share, warning-sign presence, findings, patterns), the release split (previous vs latest release in the window, per rule), warning-sign impact (pass rate with the sign present vs absent), and patterns by theme. |
| `GET …/insights/procedures/<id>/rules/<rule_id>` | The rule's wordings over time, its pass rate overall, per release and per agent (with intervals), its patterns, and the 5 most recent findings with their quotes. |
| `GET …/insights/releases/compare?a=&b=` | Release B against A (default: the latest release with sessions in the window, and the one before): per procedure and per rule, both rates with n and intervals, the delta in points (null under the minimum n), a flag, and whether the rule is new, removed or reworded. |
| `GET …/insights/report` | The review report: headline numbers (plus sessions with problems on more than one rule), then per procedure its themes, each with its patterns: the rule they're about, failure modes, example phrasings and the sessions. |

## Onboarding

Importing existing agent prompts. Both ways end in a **proposal** (agents, shared instructions, procedures, groups, findings, and per-agent fidelity from `sopc compare`) that the user reviews and applies as drafts.

| Endpoint | What it does |
|---|---|
| `GET /v1/onboarding/capabilities` | `{llm, model, platforms}`: whether sopops can extract with its own model. |
| `POST /v1/onboarding/platform-agents` | `{platform: elevenlabs\|vapi, api_key}`: the agents the key can see. The key is used for this request only. |
| `GET/POST …/imports` | List imports; start one with sopops's model: `{sources: [{name, platform, platform_id?, prompt, tools}], platform?: {platform, api_key, agent_ids}}`. `503` if no model is configured. Runs in the background. |
| `POST …/imports/agent` | `{mode: hosted\|repo, repo?, server_url?}`: an onboarding token for Claude Code or Codex, shown once, plus `kickoff` (MCP commands, prompt, settings). |
| `GET …/imports/<id>` | Status (`waiting`, `running`, `ready`, `failed`, `applied`, `discarded`), token state, and the proposal. |
| `GET …/imports/<id>/events?after=N`, `GET …/imports/<id>/stream` | Progress messages; the stream is server-sent events. |
| `PUT …/imports/<id>/proposal` | Save edits; fidelity is checked again. |
| `POST …/imports/<id>/findings/<finding>` | `{choice}`: `keep`, or the agent whose version every agent gets. |
| `POST …/imports/<id>/apply` | Write the proposal as drafts (items with the same id are replaced). Ends the token. |
| `POST …/imports/<id>/revoke`, `DELETE …/imports/<id>` | End the token (Finish); discard the import. |

With `Authorization: Bearer <onboarding token>` (scope `onboarding:write`, one workspace, 2 hours, stored hashed; `401` when unknown, expired or revoked, `403` without the scope):

| Endpoint | What it does |
|---|---|
| `GET /v1/onboarding/session` | The workspace, import, mode and repo the token is for. |
| `POST /v1/onboarding/progress` | `{step, message}`: shown live on the import page. |
| `PUT /v1/onboarding/proposal` | `{proposal, originals, findings?, notes?}`: replaces the import's proposal; returns fidelity. |
| `POST /v1/onboarding/fidelity` | `{proposal, originals}`: fidelity only, nothing stored. |
