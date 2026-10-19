# Import your agents

Already running agents? **Import** brings their instructions into sopops: each agent's own text, shared instructions, procedures, and `{{variables}}` where agents differ only by a value. Where they disagree on a rule you pick one or keep both. Every agent is compiled and compared with its original sentence by sentence, so nothing is dropped silently. Applying writes drafts; you publish as usual.

**With Claude Code or Codex (recommended).** The Import page gives you a 2-hour onboarding token (this workspace only; it can send progress and a proposal, nothing else) and one command to run in your agents' repo:

```sh
claude mcp add sopops --env SOPOPS_URL=http://127.0.0.1:8484 --env SOPOPS_TOKEN=sopops_onb_... \
  -- uvx --from 'sopops-client[mcp] @ git+https://github.com/amanmibra/sopops@main#subdirectory=sdks/python' sopops-mcp \
  && claude "Onboard this project's agents into sopops: call the sopops get_kickoff tool and follow it."
```

(Codex: the same with `codex mcp add` and `codex "…"`.) Your agent finds the instructions wherever they live (code, prompt files, config, a database, ElevenLabs or Vapi), extracts them with the `sopops-mcp` tools, checks fidelity and pushes the proposal, while you watch on the import page. **In my repo too** (repo mode) connects GitHub right there, with the same form as Settings, and sopops looks in the folder (default `sops/`) on the base branch: a sopc project sopc builds is offered for import as it is; with none, the coding agent also opens the onboarding pull request adding it (nothing merges without you), reports it, and sopops follows it and pulls the folder into drafts when you merge it; one sopc can't build shows sopc's errors, to import it anyway and fix it, or have the agent rewrite it in a pull request. **In sopops** can connect GitHub too (optional), so suggested fixes arrive as pull requests. `sopops-mcp` ships in the Python client SDK ([sdks/python](../sdks/python), its `mcp` extra), so `uvx` installs only the client and the MCP library, not the server; it asks sopops over HTTP to compile, check and write files (`/v1/onboarding/…`, [API.md](../API.md#onboarding)). The instructions are [skills/sopops-onboard/SKILL.md](../skills/sopops-onboard/SKILL.md); `sopops-mcp install-skill --url http://127.0.0.1:8484` copies your server's copy into the repo (`--claude`, `--codex`), to start with `claude "/sopops-onboard"` or `codex '$sopops-onboard'`. `SOPOPS_MCP_SOURCE` on the server changes what `--from` installs (e.g. `/path/to/sopops/sdks/python[mcp]` for a local clone).

**An existing sopc project.** Already keep one, or export one from wherever your agents' instructions live? Import → **A sopc project**: pick its folder (or read it from the connected GitHub repository) and add each agent's original instructions. sopops runs the same fidelity check (sentences kept, rules word for word, similarity, missing lines) and applying writes exactly the project's files as drafts (`POST …/imports/sopc`, [API.md](../API.md#onboarding)). The [walkthrough](../examples/walkthrough/WALKTHROUGH.md#2-onboard-the-agent-repo) shows both ways.

**Paste, upload, ElevenLabs or Vapi.** sopops extracts with its own model (`SOPOPS_MODEL_REASONING`; `rules` tries it without a model). Platform keys are used for that request only, never stored or logged.
