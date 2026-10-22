# Releases

A release is a snapshot of every agent's instructions. These endpoints publish and roll back releases, serve the live one when a call starts, and push it to agents on ElevenLabs or Vapi.

## Publish and roll back

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/draft` | Unpublished changes since the current release. See below. |
| `POST /v1/workspaces/{ws}/releases` | `{author?, note?}`: release head and serve it. Returns the current release if nothing changed. |
| `GET /v1/workspaces/{ws}/releases`, `GET /v1/workspaces/{ws}/releases/{n}` | Release history; one release with every agent's instructions. |
| `POST /v1/workspaces/{ws}/releases/{n}/activate` | Serve an older release (roll back). |
| `POST /v1/workspaces/{ws}/publish` | `{files, author?, note?}`: replace head and release in one call, for CI. |
| `POST /v1/workspaces/{ws}/migrate` | `{apply?, author?, note?}`: convert a workspace stored in an older sopc format with `sopc migrate`. See below. |

**`GET /draft` returns:**
- files and `items` changed since the current release, each item with its fields `before` and `after`;
- agents added, changed, removed or unchanged, with the draft and released instructions;
- `lint`: what `sopc lint` finds in the draft (duplicated or conflicting instructions). It's advisory: publishing isn't blocked.

A release in an older format is compared the way `sopc migrate` converts it.

**`POST /migrate`** converts the v0.0.8 format, or removes `locked` (v0.0.9). It returns `{needed, plan, applied, changed}`. With `apply`, the converted files are written as a draft.

## Serve instructions at call start

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/agents` | Agents in the current release (`?release=N` for another). |
| `GET /v1/workspaces/{ws}/agents/{agent}` | **Call start.** `{agent, platform_ref, release, hash, prompt, tools, tool_sops, components}`. `agent` is the sopc id, the platform ref (`livekit:42`), or the platform id alone (`42`) when one agent has it; two agents sharing one across platforms answer `422` with both refs. `?release=N` pins a release (A/B). Logs a fetch. |
| `GET /v1/workspaces/{ws}/agents/{agent}/prompt` | Just the instructions as text, with `X-Sopc-Release` and `X-Sopc-Hash` headers. Logs a fetch. |
| `GET /v1/workspaces/{ws}/agents/{agent}/sops/{id}` | What a `get_sop` tool returns, for SOPs with `delivery: auto` or `tool`. |
| `GET /v1/workspaces/{ws}/fetches?agent=` | Which release and instructions hash each agent was served, newest first. |

`<agent>` is the sopc agent id (`tonys-pizza`) or its platform ref (`livekit:tonys-pizza`).

`components` lists each file the instructions were built from, in order, at its version: `agent`, `instruction`, `sop`, plus `sopc.yaml` as `config` (`base` in releases made before sopc v0.0.9).

**Auth.** `GET /v1/workspaces/{ws}/agents/{agent}`, `/v1/workspaces/{ws}/prompt` and `/v1/workspaces/{ws}/sops/{id}` take a token with the `runtime` scope ([Tokens](teams-and-access.md#tokens)) as `Authorization: Bearer <token>` once the workspace has one. Without it they answer `401`, and `403` for a token without the scope. A workspace without an unrevoked runtime token serves them openly (local mode).

## Deliver to platforms

Each agent gets its instructions by **pull** (the agent, or its platform's call-start webhook, fetches them), **push** (sopops writes them into the agent on ElevenLabs or Vapi when a release goes live), or both.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/runtime/delivery` | Every agent at head: `{agent, platform, platform_id, mode, modes}`. No setting means `pull`. LiveKit agents only pull. |
| `PUT /v1/workspaces/{ws}/runtime/delivery/{agent}` | `{mode: pull \| push \| both}`. `400` for a mode the platform doesn't allow. |
| `GET /v1/workspaces/{ws}/runtime/platforms` | For `elevenlabs` and `vapi`: `{api_key, webhook_secret}`, each `{set, hint, created_at}`. Never the values. |
| `PUT /v1/workspaces/{ws}/runtime/platforms/{platform}/api-key` | `{value}`: store the platform API key, encrypted with `SOPOPS_SECRET_KEY` (Fernet). `503` if that isn't set. |
| `PUT /v1/workspaces/{ws}/runtime/platforms/{platform}/webhook-secret` | `{value?}`: store a webhook secret. Without a value, one is made and returned once as `secret`. |
| `DELETE /v1/workspaces/{ws}/runtime/platforms/{platform}/<api-key \| webhook-secret>` | Remove it. |
| `POST /v1/workspaces/{ws}/runtime/platforms/{platform}/test` | `{platform_id?}`: read one agent with the stored key (a GET; default: the first agent at head on that platform). Returns `{ok, platform_id, message}`. |
| `GET /v1/workspaces/{ws}/releases/{n}/pushes` | Each push-mode agent's latest push of the release: `{agent, platform, platform_id, status: ok \| unchanged \| error, version_id, error, attempts, at}`. |
| `POST /v1/workspaces/{ws}/releases/{n}/pushes/retry` | `{agents?}`: push again (default: the agents whose last push failed). `409` unless the release is live. |
| `POST /v1/workspaces/{ws}/runtime/served` | `{platform, call_id, agent, release}`, with a runtime-scoped token. A client that fetched the instructions itself reports the call it served, so the session links to the release. |
| `POST /v1/workspaces/{ws}/runtime/elevenlabs/conversation-init` | ElevenLabs' conversation-initiation webhook. See below. |
| `POST /v1/workspaces/{ws}/runtime/vapi/assistant-request` | Vapi's server URL. See below. |

**ElevenLabs conversation-init.** `?agent=` and `?release=` are optional; by default it serves the agent named by `agent_id` in the body, from the live release. It returns `{type: "conversation_initiation_client_data", dynamic_variables: {sopops_release, sopops_agent}, conversation_config_override: {agent: {prompt: {prompt}}}}`.

**Vapi assistant-request.** `?agent=` is needed if the release has more than one Vapi agent; `?release=` is optional. For `assistant-request` it returns `{assistantId, assistantOverrides: {model, variableValues: {sopops_prompt, sopops_release, sopops_agent}, metadata}}`. `model` is the assistant's own, read with the stored key and cached 5 minutes, with the system message replaced. Without a key it returns only `variableValues`; use `{{sopops_prompt}}` as the system message. Other message types get `{}`.

### How push works

Creating a release (`POST /v1/workspaces/{ws}/releases`, `/v1/workspaces/{ws}/publish`) or activating one pushes it to every push-mode agent, after the response.

- **ElevenLabs:** `GET /v1/convai/agents/{id}`, then `PATCH` with `{conversation_config: {agent: {prompt: {prompt}}}}`. An agent whose workflow has nodes besides start and end is refused, since its main prompt isn't all of its instructions.
- **Vapi:** `GET /assistant/<id>`, then `PATCH {model}` with every model field kept and the one system message replaced. It's added if there's none, and refused if there are several.

Instructions that are already live are recorded as `unchanged` without a write. `version_id` is ElevenLabs' `version_id`, or Vapi's `latestVersion` when returned.

### Webhook authentication

A request is accepted with any of these:
- the platform's webhook secret, as `Authorization: Bearer`, `X-SopOps-Secret` or `X-Vapi-Secret`;
- an HMAC-SHA256 signature: ElevenLabs' `ElevenLabs-Signature: t=…,v0=…` over `"{t}.{body}"` (at most 30 minutes old), or Vapi's `x-signature` (hex) and `x-timestamp` over `"{timestamp}.{body}"` (within 5 minutes);
- a token with the `runtime` scope, as a bearer.

A workspace with neither a webhook secret nor a runtime token accepts webhooks unauthenticated (local mode), unless `SOPOPS_REQUIRE_WEBHOOK_SECRET` is true. Then they get `401`. Set it when sopops is hosted.

### Linking sessions to releases

Each serve, by a webhook or reported to `/v1/workspaces/{ws}/runtime/served`, is kept for 7 days keyed by the call id: Vapi's `call.id`, or ElevenLabs' `conversation_id` or `call_sid`. A session ingested without a `release` takes it from there, matching `external_id`, `metadata.call_sid` or `metadata.phone_call.call_sid`.
