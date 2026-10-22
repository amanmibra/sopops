# Releases

A release is a snapshot of every agent's instructions. Publish and roll back, serve the live one when a call starts, and push it to agents on ElevenLabs or Vapi.

## Publish and roll back

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/draft` | Unpublished changes since the current release. Fields below. |
| `POST /v1/workspaces/{ws}/releases` | `{author?, note?}`: release head and serve it. Returns the current release with `created: false` if nothing changed. |
| `GET /v1/workspaces/{ws}/releases` | Release history, newest first. |
| `GET /v1/workspaces/{ws}/releases/{n}` | One release with every agent's instructions. Doesn't log a fetch. |
| `POST /v1/workspaces/{ws}/releases/{n}/activate` | Serve an older release (roll back). |
| `POST /v1/workspaces/{ws}/publish` | `{files, author?, note?}`: replace head and release in one call, for CI. |
| `POST /v1/workspaces/{ws}/migrate` | `{apply?, author?, note?}`: convert a workspace stored in an older sopc format with `sopc migrate`. Returns `{workspace, needed, plan, applied, changed}`. It handles the v0.0.8 format, and `locked` (v0.0.9). With `apply`, the converted files are written as a draft. |

```sh
curl -s -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/releases" \
  -d '{"author": "Ada", "note": "Read-back fix"}'
```

```json
{
  "release": {
    "number": 4,
    "created_at": "2026-10-19T16:53:14.360206Z",
    "author": "Ada",
    "note": "Read-back fix",
    "files": {"agents/copper-wok-harbor.yaml": 1, "procedures/delivery-orders.md": 4, "sopc.yaml": 1},
    "current": true
  },
  "created": true
}
```

### What `GET /draft` returns

| Field | Type | |
|---|---|---|
| `release` | int | The current release the draft is compared with. |
| `valid`, `issues` | bool, list | Whether head compiles, and sopc's issues. |
| `files` | `[{path, change, version, released_version}]` | Files added, edited or deleted since the current release. |
| `items` | `[{kind, id, name, change, version, released_version, before, after}]` | The same changes as fields: `before` is the item in the release, `after` is the draft. |
| `agents` | `[{agent, platform_ref, status, hash, released_hash, prompt, released_prompt, components}]` | `status` is `added`, `changed`, `removed` or `unchanged`. |
| `lint` | `[{code, message, sources, agents}]` | What `sopc lint` finds in the draft (duplicated or conflicting instructions). Advisory: publishing isn't blocked. |

A release in an older format is compared the way `sopc migrate` converts it.

## Serve instructions at call start

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/agents` | Agents in the current release (`?release=N` for another): `{workspace, release, agents: [{agent, platform_ref, hash}]}`. |
| `GET /v1/workspaces/{ws}/agents/{agent}` | **Call start.** `{agent, platform_ref, release, hash, prompt, tools, tool_sops, components}`. `?release=N` pins a release (A/B). Logs a fetch. |
| `GET /v1/workspaces/{ws}/agents/{agent}/prompt` | Just the instructions as text, with `X-Sopc-Release` and `X-Sopc-Hash` headers. Logs a fetch. |
| `GET /v1/workspaces/{ws}/agents/{agent}/sops/{id}` | What a `get_sop` tool returns, for SOPs with `delivery: auto` or `tool`. `404` for a SOP that isn't tool-delivered to that agent. |
| `GET /v1/workspaces/{ws}/fetches?agent=&limit=` | Which release and instructions hash each agent was served, newest first: `[{id, agent, release, hash, at}]`. Default limit 100, at most 1000. |

```sh
curl -s -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/agents/copper-wok-harbor"
```

```json
{
  "agent": "copper-wok-harbor",
  "platform_ref": "livekit:copper-wok-harbor",
  "release": 4,
  "hash": "3dde496a3735855e509af9b7e5b86a73161b8605106838dd31378f5fc8a7404c",
  "prompt": "Copper Wok Harbor is a noodle and wok counter by the ferry terminal...",
  "tools": ["check_delivery_zone", "lookup_allergens", "place_order", "transfer_to_staff"],
  "tool_sops": [],
  "components": [
    {"kind": "agent", "id": "copper-wok-harbor", "path": "agents/copper-wok-harbor.yaml", "version": 1},
    {"kind": "sop", "id": "delivery-orders", "path": "procedures/delivery-orders.md", "version": 4},
    {"kind": "config", "id": "sopc", "path": "sopc.yaml", "version": 1}
  ]
}
```

| Rule | |
|---|---|
| Naming an agent | The sopc id (`copper-wok-harbor`), its platform ref (`livekit:copper-wok-harbor`), or the platform id alone (`42`) when one agent has it. Two agents sharing one across platforms answer `422` with both refs. |
| `components` | Each file the instructions were built from, in order, at its version: `agent`, `instruction`, `sop`, plus `sopc.yaml` as `config` (`base` in releases made before sopc v0.0.9). |
| Auth | `/agents/{agent}`, `/agents/{agent}/prompt` and `/agents/{agent}/sops/{id}` take a token with the `runtime` scope ([Tokens](teams-and-access.md#tokens)) once the workspace has one. Without it they answer `401`, and `403` for a token without the scope. A workspace with no unrevoked runtime token serves them openly (local mode). |

## Deliver to platforms

Each agent gets its instructions by **pull** (the agent, or its platform's call-start webhook, fetches them), **push** (sopops writes them into the agent on ElevenLabs or Vapi when a release goes live), or both.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/runtime/delivery` | Every agent at head: `[{agent, platform, platform_id, mode, modes}]`. No setting means `pull`. LiveKit agents only pull. |
| `PUT /v1/workspaces/{ws}/runtime/delivery/{agent}` | `{mode: pull \| push \| both}`. `400` for a mode the platform doesn't allow. |
| `GET /v1/workspaces/{ws}/runtime/platforms` | For `elevenlabs` and `vapi`: `{api_key, webhook_secret}`, each `{set, hint, created_at}`. Never the values. |
| `PUT /v1/workspaces/{ws}/runtime/platforms/{platform}/api-key` | `{value}`: store the platform API key, encrypted with `SOPOPS_SECRET_KEY` (Fernet). `503` if that isn't set. |
| `PUT /v1/workspaces/{ws}/runtime/platforms/{platform}/webhook-secret` | `{value?}`: store a webhook secret. Without a value, one is made and returned once as `secret`. |
| `DELETE /v1/workspaces/{ws}/runtime/platforms/{platform}/<api-key \| webhook-secret>` | Remove it. |
| `POST /v1/workspaces/{ws}/runtime/platforms/{platform}/test` | `{platform_id?}`: read one agent with the stored key (a GET; default: the first agent at head on that platform). Returns `{ok, platform_id, message}`. |
| `GET /v1/workspaces/{ws}/releases/{n}/pushes` | Each push-mode agent's latest push of the release: `[{release, agent, platform, platform_id, status: ok \| unchanged \| error, version_id, error, attempts, at}]`. |
| `POST /v1/workspaces/{ws}/releases/{n}/pushes/retry` | `{agents?}`: push again (default: the agents whose last push failed). `409` unless the release is live. |
| `POST /v1/workspaces/{ws}/runtime/served` | `{platform, call_id, agent, release}`, with a runtime-scoped token. A client that fetched the instructions itself reports the call it served, so the session links to the release. |
| `POST /v1/workspaces/{ws}/runtime/elevenlabs/conversation-init` | ElevenLabs' conversation-initiation webhook. |
| `POST /v1/workspaces/{ws}/runtime/vapi/assistant-request` | Vapi's server URL. |

```sh
curl -s -X PUT -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/runtime/delivery/support-line" \
  -d '{"mode": "both"}'
```

```json
{"agent": "support-line", "platform": "elevenlabs", "platform_id": "ag_42", "mode": "both", "modes": ["pull", "push", "both"]}
```

**ElevenLabs conversation-init.** `?agent=` and `?release=` are optional; by default it serves the agent named by `agent_id` in the body, from the live release.

```json
{
  "type": "conversation_initiation_client_data",
  "dynamic_variables": {"sopops_release": 4, "sopops_agent": "support-line"},
  "conversation_config_override": {"agent": {"prompt": {"prompt": "You are the phone host..."}}}
}
```

**Vapi assistant-request.** `?agent=` is needed if the release has more than one Vapi agent; `?release=` is optional. For `assistant-request` it returns `{assistantId, assistantOverrides: {model, variableValues: {sopops_prompt, sopops_release, sopops_agent}, metadata}}`. `model` is the assistant's own, read with the stored key and cached 5 minutes, with the system message replaced. Without a key it returns only `variableValues`; use `{{sopops_prompt}}` as the system message. Other message types get `{}`.

### How push works

Creating a release (`POST /v1/workspaces/{ws}/releases`, `/v1/workspaces/{ws}/publish`) or activating one pushes it to every push-mode agent, after the response.

| Platform | What sopops calls | Refused when |
|---|---|---|
| ElevenLabs | `GET /v1/convai/agents/{id}`, then `PATCH` with `{conversation_config: {agent: {prompt: {prompt}}}}`. | The agent's workflow has nodes besides start and end, so its main prompt isn't all of its instructions. |
| Vapi | `GET /assistant/<id>`, then `PATCH {model}` with every model field kept and the one system message replaced. | There are several system messages. With none, one is added. |

Instructions that are already live are recorded as `unchanged` without a write. `version_id` is ElevenLabs' `version_id`, or Vapi's `latestVersion` when returned.

### Webhook authentication

A request is accepted with any of these:

| Credential | How it's sent |
|---|---|
| The platform's webhook secret | `Authorization: Bearer`, `X-SopOps-Secret` or `X-Vapi-Secret`. |
| An HMAC-SHA256 signature | ElevenLabs' `ElevenLabs-Signature: t=…,v0=…` over `"{t}.{body}"`, at most 30 minutes old. Vapi's `x-signature` (hex) with `x-timestamp` over `"{timestamp}.{body}"`, within 5 minutes. |
| A token with the `runtime` scope | As a bearer. |

A workspace with neither a webhook secret nor a runtime token accepts webhooks unauthenticated (local mode), unless `SOPOPS_REQUIRE_WEBHOOK_SECRET` is true. Then they get `401`. Set it when sopops is hosted.

### Linking sessions to releases

Each serve, by a webhook or reported to `/v1/workspaces/{ws}/runtime/served`, is kept for 7 days keyed by the call id: Vapi's `call.id`, or ElevenLabs' `conversation_id` or `call_sid`. A session ingested without a `release` takes it from there, matching `external_id`, `metadata.call_sid` or `metadata.phone_call.call_sid`.
