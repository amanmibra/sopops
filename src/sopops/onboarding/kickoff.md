Onboard this project's voice agents into sopops (workspace `$workspace` at $server_url). The sopops MCP server is connected; its tools do the compiling and checking.

1. Call `get_extraction_guide` and follow it. Call `report_progress` with a short message at each step below, so the person watching the sopops page can follow along.
2. Find every agent's instructions, wherever they live: agent code, prompt files, env or config, or a database (query it read-only with this project's existing tooling). For ElevenLabs or Vapi agents, `list_platform_agents` and `get_agent_prompt` read them if the key is set. Read only: don't change, deploy or delete anything, and never print or copy secrets.
3. Build one proposal for all agents: each agent's own context, shared instructions, procedures (goal, when, steps with tool names, never, warning signs), `{{variables}}` for values that differ between agents, and findings for drift (agents that disagree on a rule). Keep the original wording.
4. Call `check_fidelity` with the proposal and the original prompts. Fix every missing or changed sentence and check again until each agent passes, or explain what's left.
5. Call `push_to_sopops` with the proposal, the originals and your findings. Pushing again replaces what you pushed before. The person reviews it in sopops and applies it as drafts; you can't publish.
$repo_step
Finish with a short summary: agents found (and where), blocks extracted, drift found, and each agent's fidelity score.
