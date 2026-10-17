---
name: sopops-onboard
description: Onboard a team's voice agents into sopops from their repo. Finds every agent's instructions (code, prompt files, config, a database, or ElevenLabs/Vapi), extracts agent context, shared instructions and procedures with the sopops-mcp tools, checks fidelity, pushes the proposal to sopops for review, and optionally opens a PR adding a sopc project at sops/. Use when the user asks to onboard, import or migrate agent prompts into sopops, or pastes a sopops kickoff prompt.
---

# Onboard voice agents into sopops

The `sopops-mcp` server does the compiling and checking; you do the finding and the judgment; the user reviews in sopops.

## Setup (once)

The sopops **Import agents** page (Use Claude Code / Codex) gives you the exact commands, with the server URL and a 2-hour onboarding token filled in. By hand:

```sh
# Claude Code
claude mcp add sopops --env SOPOPS_URL=https://sopops.example --env SOPOPS_TOKEN=sopops_onb_... \
  -- uvx --from git+https://github.com/amanmibra/sopops@onboarding sopops-mcp
# Codex
codex mcp add sopops --env SOPOPS_URL=https://sopops.example --env SOPOPS_TOKEN=sopops_onb_... \
  -- uvx --from git+https://github.com/amanmibra/sopops@onboarding sopops-mcp
```

Cursor (`.cursor/mcp.json`):

```json
{"mcpServers": {"sopops": {"command": "uvx", "args": ["--from", "git+https://github.com/amanmibra/sopops@onboarding", "sopops-mcp"],
  "env": {"SOPOPS_URL": "https://sopops.example", "SOPOPS_TOKEN": "sopops_onb_..."}}}}
```

Optional environment: `ELEVENLABS_API_KEY` / `VAPI_API_KEY` to read agents from those platforms (read-only keys are enough), `SOPC_BIN` for a local sopc, `SOPOPS_MODEL_REASONING` to let sopops extract with its own model. Without `SOPOPS_URL`/`SOPOPS_TOKEN`, everything but `push_to_sopops` and `report_progress` still works locally.

## Tools

| Tool | What it does |
|---|---|
| `get_extraction_guide()` | The extraction rules, the proposal's JSON schema, and the session (workspace, hosted or repo mode). |
| `list_platform_agents(platform)` | ElevenLabs or Vapi agents, with the key from the environment. |
| `get_agent_prompt(platform, id)` | One agent's prompt and tool names. |
| `propose_extraction(prompts)` | sopops's extraction if a model is configured here; otherwise the instructions plus each prompt split into numbered units for you to classify. |
| `check_fidelity(proposal, originals)` | Compiles each agent with sopc and lists missing or changed sentences against its original. |
| `push_to_sopops(proposal, originals, findings?, notes?)` | Sends the proposal for review. Each push replaces the last. Nothing is published. |
| `report_progress(step, message)` | A line on the sopops page the user is watching. |
| `write_sopc_project(proposal, path)` | Writes the proposal as a sopc project (default `sops/`). |

## Flow

1. `get_extraction_guide`, then `report_progress` at each step.
2. Find every agent's prompt, read-only. Resolve templating so each original is the exact text the model gets. Note platform, platform id and tool names.
3. Build the proposal (or start from `propose_extraction`). Preserve wording; never drop text (unplaceable text goes in the agent's context).
4. `check_fidelity` until every agent passes, or explain what's left.
5. `push_to_sopops` with the originals and your drift findings. The user applies it as drafts and publishes from sopops.
6. Repo mode only (the session says `"mode": "repo"`): `write_sopc_project`, then open a PR (`gh pr create`) with the agents, blocks, drift and fidelity scores. Don't merge it.

Never change, deploy or delete anything on a platform or in a database, and never print or copy keys or the token.
