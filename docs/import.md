# Import your agents

Import brings running agents' instructions into sopops as drafts, which you publish as usual.

- Each agent's text is split into shared instructions, procedures, and <code v-pre>{{variables}}</code> where agents differ only by a value.
- Where agents disagree on a rule, you pick one or keep both.
- Each agent is compiled and compared with its original, sentence by sentence, so nothing drops silently.

## Import with Claude Code or Codex (recommended)

The Import page gives you a 2-hour onboarding token (this workspace only, can send only progress and a proposal) and one command to run in your agents' repo:

```sh
claude mcp add sopops --env SOPOPS_URL=http://127.0.0.1:8484 --env SOPOPS_TOKEN=sopops_onb_... \
  -- uvx --from 'sopops[mcp] @ git+https://github.com/amanmibra/sopops@main#subdirectory=sdks/python' sopops-mcp \
  && claude "Onboard this project's agents into sopops: call the sopops get_kickoff tool and follow it."
```

For Codex, use `codex mcp add` and `codex "…"`. The agent finds the instructions wherever they live (code, prompt files, config, a database, ElevenLabs or Vapi), extracts them, checks fidelity and pushes a proposal you watch arrive on the Import page.

**In my repo too** (repo mode) connects GitHub from the Import page, with the same form as in Settings. sopops then checks the folder (default `sops/`) on the base branch:

- **sopc project that builds:** offered for import as it is.
- **No project:** the agent opens an onboarding pull request adding one. sopops pulls the folder into drafts when you merge, and nothing merges without you.
- **Project sopc can't build:** shows sopc's errors. Import anyway and fix it, or have the agent rewrite it in a pull request.

**In sopops** can connect GitHub too, so suggested fixes arrive as pull requests.

### How the command works

- `sopops-mcp` ships in the Python client SDK ([sdks/python](../sdks/python), `mcp` extra), so `uvx` installs the client and the MCP library, not the server. It asks sopops over HTTP to compile, check and write files (`/v1/onboarding/…`, [API reference](api/import.md)).
- The agent follows [skills/sopops-onboard/SKILL.md](../skills/sopops-onboard/SKILL.md). `sopops-mcp install-skill --url http://127.0.0.1:8484` copies your server's copy into the repo (`--claude`, `--codex`). Start it with `claude "/sopops-onboard"` or `codex '$sopops-onboard'`.
- `SOPOPS_MCP_SOURCE` on the server changes what `--from` installs, e.g. `/path/to/sopops/sdks/python[mcp]` for a local clone.

## Import an existing sopc project

1. Go to Import → **A sopc project**.
2. Pick its folder, or read it from the connected GitHub repository.
3. Add each agent's original instructions, for the same fidelity check: sentences kept, rules word for word, similarity, missing lines.

Applying writes exactly the project's files as drafts (`POST /v1/workspaces/{ws}/imports/sopc`, [API reference](api/import.md)). The [walkthrough](../examples/walkthrough/WALKTHROUGH.md#2-onboard-the-agent-repo) shows both ways.

## Paste, upload, or pull from ElevenLabs or Vapi

sopops splits the instructions into blocks with its own model (`SOPOPS_MODEL_REASONING`, or `rules` to try it without a model). Platform keys are used for that one request and are never stored or logged.
