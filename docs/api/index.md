# API reference

Every sopops endpoint, grouped by what you're trying to do. Paths start with `/v1`, and `{ws}` is a workspace id (each team has one).

```sh
curl -s -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/agents/copper-wok-harbor"
```

| Where | What |
|---|---|
| `/openapi.json` | The OpenAPI spec: every field. |
| `/docs` | Try each endpoint in a browser. |
| `operationId` | Each operation's route name (`list_workspaces`, `get_agent`, `config_put_procedure`), so generated clients read well. The web UI's typed client (`web/src/client`) is generated this way. |
| Everything else | Paths outside `/v1`, `/docs` and `/openapi.json` serve the web UI. |

## Pages

| Page | What it covers |
|---|---|
| [Agents and config](agents-and-config.md) | Editing agents, shared instructions, procedures and groups; the files behind them; stateless sopc checks |
| [Releases](releases.md) | Publishing and rolling back, serving instructions when a call starts, and pushing them to ElevenLabs or Vapi |
| [Sessions](sessions.md) | Sending calls and chats to sopops, streaming, backfill, the session format, and importing a file |
| [Checks and review](checks-and-review.md) | How sessions are checked, grouped mistakes, verdicts, suggested fixes, and reworded rules |
| [Insights](insights.md) | Pass rates, trends, release comparisons, A/B tests, and model spend |
| [Teams and access](teams-and-access.md) | Sign-in, teams and members, tokens, and a team's AI keys and models |
| [Import](import.md) | Importing agents' existing instructions, including the endpoints a coding agent calls |
| [GitHub](github.md) | Connecting a repository, syncing, and the GitHub App |

## Authentication

| Caller | Sends |
|---|---|
| Agents, webhooks, coding agents | A workspace token as `Authorization: Bearer <token>` ([Tokens](teams-and-access.md#tokens)) |
| Everyone else, once sign-in is on | A signed-in session: password, GitHub, Google or email ([Sign-in](../sign-in.md)) |

With sign-in off, only tokens guard anything, so keep sopops on a private network. [Sign-in and access](teams-and-access.md#sign-in-and-access) lists which endpoints need what.

## Errors

| Status | Body |
|---|---|
| `404`, `409` | `{"detail": "..."}` |
| `422` from sopc | `{"valid": false, "issues": [...]}` |

```json
{
  "valid": false,
  "issues": [
    {
      "code": "unknown_block",
      "message": "blocks lists 'nope', which is not an instruction, SOP or group",
      "path": "agents/bayleaf-bistro.yaml",
      "severity": "error",
      "kind": "agent",
      "id": "bayleaf-bistro",
      "field": "blocks",
      "index": null,
      "text": "Lists 'nope', which isn't a shared instruction, procedure or group."
    }
  ]
}
```

| Field | Type | |
|---|---|---|
| `code`, `message`, `path`, `severity` | string | sopc's own. `severity` is `error` or `warning`. |
| `kind` | string | `agent`, `instruction`, `procedure`, `group` or `settings`. |
| `id` | string | Which one. |
| `field` | string | As the `/config` endpoints name it, e.g. `blocks`, `steps`. |
| `index` | int | Which entry of a list field. |
| `text` | string | The problem in plain words. |
