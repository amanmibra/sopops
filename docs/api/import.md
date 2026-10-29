# Import

Import agents' existing instructions, or an existing sop project. The guide is [Import your agents](../import.md).

Every way ends in a **proposal** you review and apply as drafts: agents, shared instructions, procedures, groups, findings, and per-agent fidelity.

**Fidelity** compiles each agent and compares it with its original instructions (`sop compare`, sentence by sentence):

| Field | Type | |
|---|---|---|
| `agent`, `ok` | string, bool | Which agent, and whether the comparison passed. |
| `score` | float | Share of the original's sentences kept, 0 to 1. Reworded ones count as kept. |
| `similarity` | float | Word-level similarity of the whole texts, 0 to 1. Lower when text moved. |
| `sentences` | int | Sentences in the original. |
| `missing`, `changed`, `reworded`, `added` | lists | Those sentences. |
| `rules`, `rules_verbatim` | int | Steps, never rules and warning signs the full instructions state, and how many appear in the original word for word (spacing and case aside). |
| `not_verbatim` | list | The rules that don't: reworded, merged, or not in the original. |

## Start and review an import

| Endpoint | What it does |
|---|---|
| `GET /v1/onboarding/capabilities` | `{llm, model, platforms}`: whether sopops can extract with its own model. |
| `POST /v1/onboarding/platform-agents` | `{platform: elevenlabs \| vapi, api_key}` → `[{id, name}]`: the agents the key can see. The key is used for this request only. |
| `GET/POST /v1/workspaces/{ws}/imports` | List imports, or start one with sopops's model: `{sources: [{name, platform, platform_id?, prompt, tools}], platform?: {platform, api_key, agent_ids}, guidance?}`. `guidance` (up to 2000 characters) is how the user wants their instructions organized; the model follows it when splitting and structuring, and empty means the default choices. `503` if no model is configured. Runs in the background. |
| `POST /v1/workspaces/{ws}/imports/agent` | `{mode: hosted \| repo, server_url?}`: start an import by Claude Code or Codex. Fields below. |
| `POST /v1/workspaces/{ws}/imports/sop` | `{files?, from_repo?, originals?}`: import an existing sop project. Fields below. |
| `PUT /v1/workspaces/{ws}/imports/{id}/originals` | `{originals: {agent: prompt}}`: add or replace agents' original instructions (an empty one removes them) and check fidelity again. |
| `GET /v1/workspaces/{ws}/imports/{id}` | Status (`waiting`, `running`, `ready`, `failed`, `applied`, `discarded`), token state, the proposal, and in repo mode the onboarding pull request. |
| `GET /v1/workspaces/{ws}/imports/{id}/events?after=N` | Progress messages: `[{id, at, step, message, by, client}]`. The coding agent's messages (`by: agent`) carry `client` (Claude Code, Codex) when its MCP client said which it is. |
| `GET /v1/workspaces/{ws}/imports/{id}/stream` | The same messages as server-sent events. |
| `PUT /v1/workspaces/{ws}/imports/{id}/proposal` | Save edits; fidelity is checked again. |
| `POST /v1/workspaces/{ws}/imports/{id}/findings/{finding}` | `{choice}`: `keep`, or the agent whose version every agent gets. |
| `GET /v1/workspaces/{ws}/imports/{id}/review` | The proposal as summaries, for any number of agents. Fields below. |
| `POST /v1/workspaces/{ws}/imports/{id}/review/{group}` | `{choice}`: for a disagreement, `keep` or `all:<agent>`; for unique text, `context` or `block` (a new shared instruction). Kept with the proposal by group id. |
| `PUT /v1/workspaces/{ws}/imports/{id}/variables/{name}` | `{name?, values?}`: rename a variable everywhere, or set agents' values. |
| `POST /v1/workspaces/{ws}/imports/{id}/apply` | Write the proposal as drafts (items with the same id are replaced). An imported sop project writes exactly its files. Ends the token. |
| `POST /v1/workspaces/{ws}/imports/{id}/revoke` | End the token (Finish). |
| `DELETE /v1/workspaces/{ws}/imports/{id}` | Discard the import. |

```sh
curl -s -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/imports/agent" \
  -d '{"mode": "hosted", "server_url": "http://127.0.0.1:8484"}'
```

```json
{
  "import": {
    "id": "07f923ca-fc6e-4c06-92e4-630af5783b52",
    "workspace": "harbor-qa",
    "source": "agent",
    "status": "waiting",
    "mode": "hosted",
    "agents": [],
    "pushes": 0,
    "token": {"prefix": "sopops_onb_lzmrxf", "scopes": ["onboarding:write"], "expires_at": "2026-10-22T04:56:35Z", "revoked_at": null, "active": true},
    "agent_client": null,
    "proposal": null
  },
  "token": "sopops_onb_lzmrxf...",
  "kickoff": {
    "claude_code": "claude mcp add sopops --env SOPOPS_URL=... -- uvx ... sopops-mcp && claude 'Onboard this project...'",
    "codex": "codex mcp add sopops --env SOPOPS_URL=... -- uvx ... sopops-mcp && codex 'Onboard this project...'",
    "codex_toml": "[mcp_servers.sopops]\ncommand = \"uvx\"\n...",
    "prompt": "Onboard this project's agents into sopops: call the sopops get_kickoff tool and follow it.",
    "settings_claude_code": "Use the strongest model you have (for example /model opus) and accept edits automatically (Shift+Tab).",
    "settings_codex": "Use high reasoning effort and auto mode (codex -c model_reasoning_effort=high, then /approvals: Auto).",
    "mcp_source": "sopops[mcp] @ git+https://github.com/amanmibra/sopops@main#subdirectory=sdks/python"
  }
}
```

### Coding agent imports

`POST /imports/agent` returns an onboarding token for Claude Code or Codex, shown once, plus `kickoff`: one command per agent that adds the MCP server and starts it on `prompt`, the Codex config as TOML, and recommended settings for each.

Repo mode needs the workspace's GitHub repository: `400` without one, or when GitHub refuses the token.

- The folder is read first and recorded on the import: `repo`, `repo_branch`, `repo_path`, and `repo_project` (`missing`, `invalid` or `valid`).
- Unless `repo_project` is `valid`, the agent's kickoff tells it to open the onboarding pull request adding the project there, and to report it.
- That pull request is `pr_url`, `pr_state` (`open`, `merged`, `closed`) and `pr_merged_at`. An open one is checked on GitHub when the import is read (at most once a minute) and by the `sopops_pr_status` cron (`sopops pr-status`).
- Merged pulls the folder into drafts (`POST /v1/workspaces/{ws}/repo/pull`) and makes the import `applied`. If that pull fails, `error` says why.

### sop project imports

`POST /imports/sop` takes the project's files by path relative to the folder with `sop.yaml` (`build/` ignored), or `from_repo: true` to read the workspace's GitHub repository, and optionally each agent's original instructions.

- The import has `source: sop` and is ready at once: the proposal shows what the project holds and carries its `files`.
- Fidelity is checked for every agent with an original, as for any proposal.
- `400` without files or a `sop.yaml`; `422` when sop can't read it.
- Its content isn't edited here (`409`): change the project and import it again.

### What `GET /imports/{id}/review` has

| Field | |
|---|---|
| `counts` | Agents, shared blocks, variables, disagreements, undecided, agents with unique content, below 100% fidelity. |
| `disagreements` | Rule words that differ, from `sop overlap`: majority against outliers, smallest minority first. Then drift the model suggested beyond what overlap finds. |
| `variables` | Each variable by agent. |
| `blocks` | Who uses each proposed block, and who lacks text most agents have. |
| `unique` | Each agent's text no other has. |
| `fidelity` | Per agent, as above. |

Group ids are sop's, stable across runs.

```json
{
  "counts": {"agents": 4, "shared_blocks": 6, "variables": 6, "disagreements": 1, "undecided": 0, "agents_with_unique": 4, "below_full_fidelity": 2},
  "disagreements": [
    {
      "id": "drift-allergen-claim",
      "source": "model",
      "majority": {"text": "Never say an item is \"allergen-free\" or \"safe\"", "agents": ["bayleaf-bistro", "juniper-grill"]},
      "outliers": [{"text": "Never tell a caller that a dish is safe for their allergy", "agents": ["copper-wok-harbor"]}],
      "minority": 2,
      "words": {},
      "decision": "all:bayleaf-bistro"
    }
  ],
  "variables": [{"name": "restaurant_name", "values": {"bayleaf-bistro": "Bayleaf Bistro"}, "source": "proposal", "id": null}],
  "blocks": [{"kind": "instruction", "id": "host-role", "title": "Host role", "agents": ["bayleaf-bistro"], "missing": []}],
  "unique": [{"id": "3dc550baf287", "agent": "bayleaf-bistro", "text": "Bayleaf is a neighborhood bistro on Alder Street.", "decision": "context"}],
  "fidelity": [{"agent": "bayleaf-bistro", "ok": true, "score": 1.0, "similarity": 1.0, "sentences": 26, "rules": 10, "rules_verbatim": 10, "not_verbatim": []}]
}
```

## Endpoints for the coding agent

These take `Authorization: Bearer <onboarding token>`: scope `onboarding:write`, one workspace, 2 hours, stored hashed. `401` when it's unknown, expired or revoked; `403` without the scope.

| Endpoint | What it does |
|---|---|
| `GET /v1/onboarding/session` | The workspace, import and mode the token is for: `{workspace, import_id, mode, repo, repo_branch, repo_path, repo_project, repo_auth, pr_url, scopes, expires_at}`. |
| `GET /v1/onboarding/kickoff?server_url=` | `{instructions}`: the sopops-onboard skill and this session (what `get_kickoff` returns). |
| `GET /v1/onboarding/guide` | `{guide, proposal_schema, finding_schema}`: the extraction rules and the JSON schemas. |
| `POST /v1/onboarding/extraction` | `{prompts: [{name, platform, platform_id, prompt, tools}]}` → `{mode, instructions, inputs, originals, proposal_schema, next}`: each agent's instructions split into numbered units for the agent to classify. No model is called; nothing is stored. |
| `POST /v1/onboarding/project` | `{proposal}` → `{files, issues, validation}`: the proposal as a sop project's files, written and checked with sop. Nothing is stored. |
| `POST /v1/onboarding/progress` | `{step, message}`: shown live on the import page. |
| `PUT /v1/onboarding/proposal` | `{proposal, originals, findings?, notes?}`: replaces the import's proposal. Returns `{id, pushes, fidelity, issues}`. |
| `POST /v1/onboarding/fidelity` | `{proposal, originals}` → `{fidelity, issues}`. Nothing is stored. |
| `GET /v1/onboarding/skill` | `{name, text}`: SKILL.md, for `sopops-mcp install-skill`. No token. |
| `POST /v1/onboarding/pull-request` | `{url}`: repo mode, the onboarding pull request the agent opened (`400` unless it's a pull request in the connected repository). sopops follows it from then on. |
| `POST /v1/onboarding/pull-request/open` | `{description?}`: repo mode through the GitHub App (`repo_auth: "app"` in the session). sopops opens the onboarding pull request itself from the pushed proposal, and follows it. `POST /v1/workspaces/{ws}/imports/{id}/pull-request` does the same from the UI. |

sopops-mcp also sends `X-SopOps-Client` and `X-SopOps-Client-Version`, its MCP client's clientInfo (e.g. `claude-code`, `2.1.3`). The first request with a token is recorded on the import once (`agent_client`: name, version, label, `connected_at`), with a progress message such as "Claude Code 2.1.3 connected".

```sh
curl -s -H "Authorization: Bearer $SOPOPS_ONBOARDING_TOKEN" \
  "$SOPOPS_URL/v1/onboarding/session"
```

```json
{
  "workspace": "harbor-qa",
  "import_id": "07f923ca-fc6e-4c06-92e4-630af5783b52",
  "mode": "repo",
  "repo": "acme/voice-agents",
  "repo_branch": "main",
  "repo_path": "sops",
  "repo_project": "missing",
  "repo_auth": "app",
  "pr_url": null,
  "scopes": ["onboarding:write"],
  "expires_at": "2026-10-22T04:56:35Z"
}
```
