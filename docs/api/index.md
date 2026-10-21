# API reference

Every sopops endpoint, grouped by what you're trying to do. Paths start with `/v1`, and `{ws}` is a workspace id (each team has one).

The OpenAPI spec at `/openapi.json` lists every field, and `/docs` lets you try each endpoint. Each operation's `operationId` is its route name (`list_workspaces`, `get_agent`, `config_put_procedure`), so generated clients get readable function names. The web UI's typed client (`web/src/client`) is generated this way. Paths outside `/v1` (and `/docs`, `/openapi.json`) serve the web UI.

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

Agents, webhooks and coding agents use workspace tokens, sent as `Authorization: Bearer <token>` ([Tokens](teams-and-access.md#tokens)). Everything else needs a signed-in session once sign-in is on: passwords, GitHub, Google or email ([Sign-in](../sign-in.md)). With sign-in off, only tokens guard anything, so keep sopops on a private network. [Sign-in and access](teams-and-access.md#sign-in-and-access) lists which endpoints need what.

## Errors

A `404` or `409` has `{"detail": "..."}`.

Errors that come from sopc return `422 {"valid": false, "issues": [...]}`. Each issue has sopc's `code`, `message`, `path` and `severity`, plus where it belongs in the forms:

| Field | |
|---|---|
| `kind` | `agent`, `instruction`, `procedure`, `group` or `settings` |
| `id` | Which one |
| `field` | As the `/config` endpoints name it, e.g. `blocks`, `steps` |
| `index` | Which entry of a list field |
| `text` | The problem in plain words, e.g. "Lists 'nope', which isn't a shared instruction, procedure or group." |
