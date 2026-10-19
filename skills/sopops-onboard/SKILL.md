---
name: sopops-onboard
description: Onboard a team's voice agents into sopops from their repo. Finds every agent's instructions (code, prompt files, config, a database, or ElevenLabs/Vapi), extracts agent context, shared instructions and procedures with the sopops-mcp tools, checks fidelity, pushes the proposal to sopops for review, and optionally opens a PR adding a sopc project at sops/. Use when the user asks to onboard, import or migrate agents' instructions (prompts) into sopops, or pastes a sopops kickoff prompt.
---

# Onboard voice agents into sopops

The `sopops-mcp` server does the compiling and checking; you do the finding and the judgment; the user reviews in sopops.

When you talk to the user, call what a voice agent is told its **instructions** (what a platform may call the prompt or system prompt). "Shared instructions" is one kind of block; for everything an agent gets, say "the agent's instructions" or "full instructions".

## Setup (once)

The sopops **Import agents** page (Use Claude Code / Codex) gives you one command that adds the MCP server, with the server URL and a 2-hour onboarding token filled in, and starts the agent on this skill. By hand:

```sh
# Claude Code
claude mcp add sopops --env SOPOPS_URL=https://sopops.example --env SOPOPS_TOKEN=sopops_onb_... \
  -- uvx --from 'sopops[mcp] @ git+https://github.com/amanmibra/sopops@main#subdirectory=sdks/python' sopops-mcp
# Codex
codex mcp add sopops --env SOPOPS_URL=https://sopops.example --env SOPOPS_TOKEN=sopops_onb_... \
  -- uvx --from 'sopops[mcp] @ git+https://github.com/amanmibra/sopops@main#subdirectory=sdks/python' sopops-mcp
```

Cursor (`.cursor/mcp.json`):

```json
{"mcpServers": {"sopops": {"command": "uvx", "args": ["--from", "sopops[mcp] @ git+https://github.com/amanmibra/sopops@main#subdirectory=sdks/python", "sopops-mcp"],
  "env": {"SOPOPS_URL": "https://sopops.example", "SOPOPS_TOKEN": "sopops_onb_..."}}}}
```

`sopops-mcp` is in the sopops client SDK (`sopops`, its `mcp` extra) and does its compiling and checking through the sopops server, so it needs `SOPOPS_URL` and `SOPOPS_TOKEN`; without them only the platform tools work. Optional environment: `ELEVENLABS_API_KEY` / `VAPI_API_KEY` to read agents from those platforms (read-only keys are enough).

## Tools

| Tool | What it does |
|---|---|
| `get_kickoff()` | This skill, plus the session: workspace, server and mode (and in repo mode the connected repository, branch and folder, and whether to open the onboarding PR). |
| `get_extraction_guide()` | The extraction rules, the proposal's JSON schema, and the session (workspace, hosted or repo mode). |
| `list_platform_agents(platform)` | ElevenLabs or Vapi agents, with the key from the environment. |
| `get_agent_prompt(platform, id)` | One agent's instructions and tool names. |
| `propose_extraction(prompts)` | The extraction guide (`instructions`) plus each agent's instructions split into numbered units for you to classify, and the originals by agent id. |
| `check_fidelity(proposal, originals)` | Compiles each agent with sopc and lists missing or changed sentences against its original. |
| `push_to_sopops(proposal, originals, findings?, notes?)` | Sends the proposal for review. Each push replaces the last. Nothing is published. |
| `report_progress(step, message)` | A line on the sopops page the user is watching. |
| `write_sopc_project(proposal, path)` | Writes the proposal as a sopc project (default `sops/`), as sopops's sopc writes it, with `sopc validate`'s result. |
| `report_pull_request(url)` | Repo mode: the onboarding PR you opened. sopops follows it and pulls the folder into drafts when it merges. |
| `open_pull_request(description)` | Repo mode through sopops's GitHub App: sopops opens the onboarding PR from your pushed proposal, and follows it. |

## Flow

Onboard this project's voice agents into the sopops workspace this session is for (see "This session" in `get_kickoff`). The sopops MCP server does the compiling and checking.

1. Call `get_extraction_guide` and follow it. Call `report_progress` with a short message at each step below, so the person watching the sopops page can follow along.
2. Find every agent's instructions, wherever they live: agent code, prompt files, env or config, or a database (query it read-only with this project's existing tooling). For ElevenLabs or Vapi agents, `list_platform_agents` and `get_agent_prompt` read them if the key is set. Resolve templating so each original is the exact text the model gets. Note platform, platform id and tool names.
3. Build one proposal for all agents (or start from `propose_extraction`): each agent's own context, shared instructions, procedures (goal, when, steps with tool names, never, warning signs), `{{variables}}` for values that differ between agents, and findings for drift (agents that disagree on a rule). Keep the original wording; never drop text (text you can't place goes in the agent's context).
4. Call `check_fidelity` with the proposal and the original instructions. Fix every missing or changed sentence and check again until each agent passes, or explain what's left.
5. Call `push_to_sopops` with the proposal, the originals and your findings. Pushing again replaces what you pushed before. The person reviews it in sopops and applies it as drafts; you can't publish.
6. Repo mode only, when the session says to open the onboarding PR (the connected folder has no sopc project sopc can build): `write_sopc_project` at that folder, then open a PR into that branch (`gh pr create`) with the agents, blocks, drift and fidelity scores, and call `report_pull_request` with its URL. When the session says sopops is connected through its GitHub App, call `open_pull_request` instead (no git or `gh`). Don't merge it.

Finish with a short summary: agents found (and where), blocks extracted, drift found, and each agent's fidelity score.

Never change, deploy or delete anything on a platform or in a database, and never print or copy keys or the token.
