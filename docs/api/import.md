# Import

Import agents' existing instructions, or an existing sopc project. Every way ends in a **proposal** that you review and apply as drafts: agents, shared instructions, procedures, groups, findings, and per-agent fidelity. The guide is [Import your agents](../import.md).

**Fidelity** compiles each agent and compares it with its original instructions:

| Field | |
|---|---|
| `score` | The share of the original's sentences kept, from `sopc compare`. |
| `missing`, `changed`, `reworded` | Those sentences. |
| `similarity` | Word-level, 0 to 1. |
| `rules_verbatim` of `rules` | The steps, never rules and warning signs the full instructions state that appear in the original word for word. `not_verbatim` lists the others. |

## Start and review an import

| Endpoint | What it does |
|---|---|
| `GET /v1/onboarding/capabilities` | `{llm, model, platforms}`: whether sopops can extract with its own model. |
| `POST /v1/onboarding/platform-agents` | `{platform: elevenlabs\|vapi, api_key}`: the agents the key can see. The key is used for this request only. |
| `GET/POST /v1/workspaces/{ws}/imports` | List imports, or start one with sopops's model: `{sources: [{name, platform, platform_id?, prompt, tools}], platform?: {platform, api_key, agent_ids}}`. `503` if no model is configured. Runs in the background. |
| `POST /v1/workspaces/{ws}/imports/agent` | `{mode: hosted\|repo, server_url?}`: start an import by Claude Code or Codex. See below. |
| `POST /v1/workspaces/{ws}/imports/sopc` | `{files?, from_repo?, originals?}`: import an existing sopc project. See below. |
| `PUT /v1/workspaces/{ws}/imports/{id}/originals` | `{originals: {agent: prompt}}`: add or replace agents' original instructions (an empty one removes them) and check fidelity again. |
| `GET /v1/workspaces/{ws}/imports/{id}` | Status (`waiting`, `running`, `ready`, `failed`, `applied`, `discarded`), token state, the proposal, and in repo mode the onboarding pull request. See below. |
| `GET /v1/workspaces/{ws}/imports/{id}/events?after=N`, `GET /v1/workspaces/{ws}/imports/{id}/stream` | Progress messages; the stream is server-sent events. The coding agent's messages (`by: agent`) carry `client` (Claude Code, Codex) when its MCP client said which it is. |
| `PUT /v1/workspaces/{ws}/imports/{id}/proposal` | Save edits; fidelity is checked again. |
| `POST /v1/workspaces/{ws}/imports/{id}/findings/{finding}` | `{choice}`: `keep`, or the agent whose version every agent gets. |
| `GET /v1/workspaces/{ws}/imports/{id}/review` | The proposal as summaries, for any number of agents. See below. |
| `POST /v1/workspaces/{ws}/imports/{id}/review/{group}` | `{choice}`: for a disagreement, `keep` or `all:<agent>`; for unique text, `context` or `block` (a new shared instruction). Kept with the proposal by group id. |
| `PUT /v1/workspaces/{ws}/imports/{id}/variables/{name}` | `{name?, values?}`: rename a variable everywhere, or set agents' values. |
| `POST /v1/workspaces/{ws}/imports/{id}/apply` | Write the proposal as drafts (items with the same id are replaced). An imported sopc project writes exactly its files. Ends the token. |
| `POST /v1/workspaces/{ws}/imports/{id}/revoke`, `DELETE /v1/workspaces/{ws}/imports/{id}` | End the token (Finish); discard the import. |

**Coding agent imports** (`POST /imports/agent`) return an onboarding token for Claude Code or Codex, shown once, plus:
- `kickoff`: one command per agent, which adds the MCP server and starts the agent on "call get_kickoff";
- the Codex config as TOML;
- recommended settings for each (`settings_claude_code`, `settings_codex`).

Repo mode needs the workspace's GitHub repository (`400` without one, or when GitHub refuses the token). Its folder is read first and recorded on the import: `repo`, `repo_branch`, `repo_path`, and `repo_project` (`missing`, `invalid` or `valid`). Unless it's `valid`, the agent's kickoff tells it to open the onboarding pull request adding the project there, and to report it.

**sopc project imports** (`POST /imports/sopc`) take the project's files by path relative to the folder with `sopc.yaml` (`build/` ignored), or `from_repo: true` to read the workspace's GitHub repository, and optionally each agent's original instructions. The import has `source: sopc` and is ready at once: the proposal shows what the project holds and carries its `files`. Fidelity is checked for every agent with an original, as for any proposal. `400` without files or `sopc.yaml`; `422` when sopc can't read it. Its content isn't edited here (`409`): change the project and import it again.

**An import in repo mode** has the onboarding pull request: `pr_url`, `pr_state` (`open`, `merged`, `closed`) and `pr_merged_at`. An open one is checked on GitHub when the import is read (at most once a minute) and by the `sopops_pr_status` cron (`sopops pr-status`). Merged pulls the folder into drafts (`POST /v1/workspaces/{ws}/repo/pull`) and makes the import `applied`; if that pull fails, `error` says why.

**`GET /imports/{id}/review`** has:

| Field | |
|---|---|
| `counts` | Agents, shared blocks, variables, disagreements, undecided, agents with unique content, below 100% fidelity. |
| `disagreements` | Rule words that differ, from `sopc overlap`: majority against outliers, smallest minority first. Then drift the model suggested beyond what overlap finds. |
| `variables` | Each variable × agent. |
| `blocks` | Who uses each proposed block, and who lacks text most agents have. |
| `unique` | Each agent's text no other has. |
| `fidelity` | Per agent. |

Group ids are sopc's, stable across runs.

## Endpoints for the coding agent

These take `Authorization: Bearer <onboarding token>`: scope `onboarding:write`, one workspace, 2 hours, stored hashed. `401` when it's unknown, expired or revoked; `403` without the scope.

sopops-mcp also sends `X-Sopops-Client` and `X-Sopops-Client-Version`, its MCP client's clientInfo (e.g. `claude-code`, `2.1.3`). The first request with a token is recorded on the import once (`agent_client`: name, version, label, `connected_at`), with a progress message such as "Claude Code 2.1.3 connected".

| Endpoint | What it does |
|---|---|
| `GET /v1/onboarding/session` | The workspace, import and mode the token is for. In repo mode, also the repository, branch, folder, what the folder held, and `pr_url`. |
| `GET /v1/onboarding/kickoff?server_url=` | `{instructions}`: the sopops-onboard skill and this session (what `get_kickoff` returns). |
| `GET /v1/onboarding/guide` | `{guide, proposal_schema, finding_schema}`: the extraction rules and the JSON schemas. |
| `POST /v1/onboarding/extraction` | `{prompts: [{name, platform, platform_id, prompt, tools}]}`: the extraction guide (`instructions`), each agent's instructions split into numbered units for the agent to classify, and `originals` by agent id. No model is called; nothing is stored. |
| `POST /v1/onboarding/project` | `{proposal}`: the proposal as a sopc project's files (`{files, issues, validation}`), written and checked with sopc. Nothing is stored. |
| `POST /v1/onboarding/progress` | `{step, message}`: shown live on the import page. |
| `PUT /v1/onboarding/proposal` | `{proposal, originals, findings?, notes?}`: replaces the import's proposal. Returns fidelity. |
| `POST /v1/onboarding/fidelity` | `{proposal, originals}`: fidelity only. Nothing is stored. |
| `GET /v1/onboarding/skill` | `{name, text}`: SKILL.md, for `sopops-mcp install-skill`. No token. |
| `POST /v1/onboarding/pull-request` | `{url}`: repo mode, the onboarding pull request the agent opened (`400` unless it's a pull request in the connected repository). sopops follows it from then on. |
| `POST /v1/onboarding/pull-request/open` | `{description?}`: repo mode through the GitHub App (`repo_auth: "app"` in the session). sopops opens the onboarding pull request itself from the pushed proposal, and follows it. `POST /v1/workspaces/{ws}/imports/{id}/pull-request` does the same from the UI. |
