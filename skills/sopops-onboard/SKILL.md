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
| `get_ab_test_guide()` | How to set up an A/B test of an agent's models, voice or instructions (the sopops-ab-tests skill). |
| `open_pull_request(description)` | Repo mode through sopops's GitHub App: sopops opens the onboarding PR from your pushed proposal, and follows it. |
| `list_sops()` | A map of every agent, instruction, procedure and group at the current draft head. Start here for later SOP work. |
| `get_sop(kind, id)` | One agent, instruction, procedure or group in full. Read it before editing or deleting it. |
| `get_sop_settings()` | Workspace-wide placeholder defaults and the procedures heading, which can affect every compiled agent. |
| `save_sop(kind, id, fields, note?)` | Create or replace an agent, instruction, procedure or group as a draft. The full configuration is validated before saving. |
| `save_sop_settings(fields, note?)` | Update workspace-wide defaults as a draft. |
| `delete_sop(kind, id, note?)` | Delete a draft item only after explaining its `used_by` impact and receiving an explicit user request. |
| `get_sop_history(kind, id)` / `get_sop_version(kind, id, version, file?)` | Inspect an item's earlier versions; restore one by reading it and saving its fields again. |
| `preview_agent(agent)` | Compile one agent at draft head and compare it with the live release. |

## Flow

Onboard this project's voice agents into the sopops workspace this session is for (see "This session" in `get_kickoff`). The sopops MCP server does the compiling and checking.

1. Call `get_extraction_guide` and follow it. Call `report_progress` with a short message at each step below, so the person watching the sopops page can follow along.
2. **Get each agent's identifier right.** It's how their code will ask sopops for that agent, so take it from the
   code that starts a call, not from a heading or a folder name. On LiveKit that's whatever they dispatch with:
   often a row id from their own database, sometimes the LiveKit agent name. On ElevenLabs or Vapi it has to be the
   platform's own id, so pushes and webhooks match. If two places disagree, or you can't tell which value reaches
   the agent at call start, ask rather than guess: a wrong identifier means calls fetch the wrong instructions.
   Keep the sopc id readable (`copper-wok-harbor`) and put the real identifier in `platform_id`; they don't have to
   match, and sopops resolves either.
3. Find every agent's instructions, wherever they live: agent code, prompt files, env or config, or a database (query it read-only with this project's existing tooling). For ElevenLabs or Vapi agents, `list_platform_agents` and `get_agent_prompt` read them if the key is set. Resolve templating so each original is the exact text the model gets. Note platform, platform id and tool names.
4. Before you propose, ask the user how they want their instructions organized (e.g. "keep each restaurant's menu as its own block", "everything about refunds is one procedure"), and follow their answer when you split, group and name blocks, without dropping or rewording text; no preference means the defaults below. Build one proposal for all agents (or start from `propose_extraction`): each agent's own context, shared instructions, procedures (goal, when, steps with tool names, never, warning signs), `{{variables}}` for values that differ between agents, and findings for drift (agents that disagree on a rule). Keep the original wording; never drop text (text you can't place goes in the agent's context).
5. Call `check_fidelity` with the proposal and the original instructions. Fix every missing or changed sentence and check again until each agent passes, or explain what's left.
6. Call `push_to_sopops` with the proposal, the originals and your findings. Pushing again replaces what you pushed before. The person reviews it in sopops and applies it as drafts; you can't publish.
7. Repo mode only, when the session says to open the onboarding PR (the connected folder has no sopc project sopc can build): `write_sopc_project` at that folder, then open a PR into that branch (`gh pr create`) with the agents, blocks, drift and fidelity scores, and call `report_pull_request` with its URL. When the session says sopops is connected through its GitHub App, call `open_pull_request` instead (no git or `gh`). Don't merge it.

## Many agents

Past about ten agents, or with long instructions, work in batches of five to ten.

1. **Extract in parallel if you like.** Reading files and classifying text sends nothing to sopops, so split that work however is fastest.
2. **Check each batch as you go.** `check_fidelity` stores nothing, so calling it per batch is safe and keeps each request small. Fix a batch before extracting the next: the same mistake usually repeats across agents, so an early fix saves the rest.
3. **Keep one proposal.** Merge each batch into it. Blocks are shared, so a procedure found in the first batch gets reused by later agents instead of duplicated. Watch for the same text under two ids, which is what duplicates look like.
4. **Push once, at the end.** `push_to_sopops` replaces everything pushed before, so two pushes at once, or one per batch from different workers, leave only the last. Never push in parallel.
5. **Report progress after each batch** ("checked 20 of 48 agents"), so the person watching the Import page sees it moving.

A push carries every agent's original text, so a large one takes a while; the client waits two minutes. If it times out, don't split the push. Cut what isn't needed (drop agents you haven't finished, push those in a later run) and try again.

## Changing SOPs later

Onboarding runs once. After it, the same MCP server keeps a workspace's SOPs up to date from the repo, with a
workspace token that has the `config` scope (sopops Settings → Tokens) and `SOPOPS_WORKSPACE` set to the workspace id:

1. `list_sops` for what exists now, then `get_sop_settings` when defaults or placeholders might matter.
2. `get_sop(kind, id)` for the item you mean to change, then `save_sop(kind, id, fields)` with the whole item, not a
   patch. sopc checks every save, so one that wouldn't compile comes back with its issues.
3. For a shared instruction, procedure or group, inspect `used_by` and preview each affected agent with
   `preview_agent(agent)` before handing it off.
4. Before deleting, inspect `used_by`, explain the effect, and get an explicit request. Use `delete_sop`; it creates
   a draft, not a live change.
5. When you need prior context, use `get_sop_history` and `get_sop_version`. Restore an older version by saving its
   fields as a new draft.

Every save is a draft. Nothing reaches a call until someone publishes a release in sopops, so end by telling the
person what changed and that it's waiting in Changes.

Finish with a short summary: agents found (and where), blocks extracted, drift found, and each agent's fidelity score. Then offer to set up an A/B test of the agents' models, voices or instructions; if the user wants one, call `get_ab_test_guide` and follow it.

Never change, deploy or delete anything on a platform or in a database, and never print or copy keys or the token.
